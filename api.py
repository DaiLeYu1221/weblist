import os
import json
from flask import Flask, request, jsonify, send_file
from pan123 import Pan123  # 导入提供的pan123模块
from werkzeug.utils import secure_filename
import threading

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = './uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 全局锁用于线程安全
lock = threading.Lock()

class Pan123API:
    def __init__(self, config_path: str = "settings.json"):
        self.config_path = config_path
        self._load_config()
        self.pan = None
        self.login()
    
    def _load_config(self):
        """加载配置文件"""
        self.config = {
            "default-path": "",
            "user": "",
            "password": "",
            "authorization": ""
        }
        
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                try:
                    config = json.load(f)
                    self.config.update(config)
                except json.JSONDecodeError:
                    pass
    
    def login(self):
        """登录并初始化网盘客户端"""
        with lock:
            self.pan = Pan123(
                readfile=True,
                user_name=self.config["user"],
                pass_word=self.config["password"],
                authorization=self.config["authorization"],
                input_pwd=False
            )
            
            login_code = self.pan.login()
            if login_code != 200:
                raise Exception(f"登录失败，错误码: {login_code}")
            else:
                self.config["authorization"] = self.pan.authorization
                self._save_config()
                self._validate_default_path()
    
    def _validate_default_path(self):
        """验证默认路径"""
        if self.config["default-path"]:
            path_parts = [p for p in self.config["default-path"].split("/") if p]
            current_dir = 0
            
            for part in path_parts:
                dir_info = self._find_folder_by_name(current_dir, part)
                if not dir_info:
                    raise Exception("主目录不合法")
                current_dir = dir_info["FileId"]
    
    def _find_folder_by_name(self, parent_id: int, name: str):
        """通过名称查找文件夹"""
        self.pan.parent_file_id = parent_id
        self.pan.get_dir()
        
        for item in self.pan.list:
            if item["Type"] == 1 and item["FileName"] == name:
                return item
        return None
    
    def _save_config(self):
        """保存配置"""
        self.config["user"] = self.pan.user_name
        self.config["password"] = self.pan.password
        self.config["authorization"] = self.pan.authorization
        
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
    
    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        if size > 1073741824:
            return f"{round(size / 1073741824, 2)}GB"
        elif size > 1048576:
            return f"{round(size / 1048576, 2)}MB"
        elif size > 1024:
            return f"{round(size / 1024, 2)}KB"
        else:
            return f"{size}B"
    
    def list(self) -> dict:
        """列出当前目录下的文件和文件夹"""
        with lock:
            self.pan.get_dir()
            folders = []
            files = []
            
            for item in self.pan.list:
                if item["Type"] == 1:  # 文件夹
                    folders.append({
                        "id": str(item["FileId"]),
                        "name": item["FileName"]
                    })
                else:  # 文件
                    files.append({
                        "id": str(item["FileId"]),
                        "name": item["FileName"],
                        "size": self._format_size(item["Size"])
                    })
            
            return {"folder": folders, "file": files}
    
    def list_folder(self, path: str) -> dict:
        """进入指定子目录"""
        with lock:
            path_parts = [p for p in path.split("/") if p]
            current_id = self.pan.parent_file_id
            
            for part in path_parts:
                dir_info = self._find_folder_by_name(current_id, part)
                if not dir_info:
                    return {"error": "没有找到对应文件夹或文件"}
                current_id = dir_info["FileId"]
            
            self.pan.parent_file_id = current_id
            self.pan.get_dir()
            return self.list()
    
    def parsing(self, file_path: str) -> dict:
        """解析文件路径获取下载链接"""
        with lock:
            path_parts = [p for p in file_path.split("/") if p]
            current_id = self.pan.parent_file_id
            
            # 定位到文件所在目录
            for part in path_parts[:-1]:
                dir_info = self._find_folder_by_name(current_id, part)
                if not dir_info:
                    return {"error": "没有找到对应文件夹或文件"}
                current_id = dir_info["FileId"]
            
            self.pan.parent_file_id = current_id
            self.pan.get_dir()
            
            file_name = path_parts[-1]
            for idx, item in enumerate(self.pan.list):
                if item["Type"] != 1 and item["FileName"] == file_name:
                    download_url = self.pan.link(idx)
                    return {"url": download_url}
            
            return {"error": "没有找到对应文件"}
    
    def reload_session(self) -> dict:
        """重新加载会话"""
        try:
            self.login()
            return {"status": "success", "message": "会话已重新加载"}
        except Exception as e:
            return {"error": str(e)}
    
    def create_folder(self, folder_name: str) -> dict:
        """创建新文件夹"""
        with lock:
            try:
                result = self.pan.new_folder(folder_name)
                if result.get("code") == 200:
                    return {"status": "success", "folder_id": str(result["data"]["fileId"])}
                return {"error": "文件夹创建失败"}
            except Exception as e:
                return {"error": str(e)}
    
    def delete(self, file_id: str) -> dict:
        """删除文件/文件夹"""
        with lock:
            try:
                result = self.pan.delete(file_id)
                if result.get("code") == 200:
                    return {"status": "success"}
                return {"error": "删除操作失败"}
            except Exception as e:
                return {"error": str(e)}
    
    def share(self, file_id: str) -> dict:
        """生成分享链接"""
        with lock:
            try:
                result = self.pan.share(file_id)
                if result.get("code") == 200:
                    return {"url": result["data"]["shareUrl"]}
                return {"error": "分享失败"}
            except Exception as e:
                return {"error": str(e)}
    
    def upload(self, file_path: str) -> dict:
        """上传本地文件"""
        with lock:
            try:
                result = self.pan.upload(file_path)
                if result.get("code") == 200:
                    return {"status": "success", "file_id": str(result["data"]["fileId"])}
                return {"error": "上传失败"}
            except Exception as e:
                return {"error": str(e)}

# 初始化API实例
api = Pan123API()

@app.route('/api/login', methods=['POST'])
def login():
    """登录接口"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if username and password:
        # 更新配置
        api.config['user'] = username
        api.config['password'] = password
        api._save_config()
    
    try:
        api.login()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 401

@app.route('/api/list', methods=['GET'])
def list_files():
    """列出当前目录文件"""
    try:
        return jsonify(api.list())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/list/<path:sub_path>', methods=['GET'])
def list_subfolder(sub_path):
    """列出子目录文件"""
    try:
        result = api.list_folder(sub_path)
        if 'error' in result:
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/parsing/<path:file_path>', methods=['GET'])
def get_download_link(file_path):
    """获取文件下载链接"""
    try:
        result = api.parsing(file_path)
        if 'error' in result:
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/share', methods=['POST'])
def share_file():
    """分享文件"""
    data = request.json
    file_id = data.get('file_id')
    
    if not file_id:
        return jsonify({"error": "缺少file_id参数"}), 400
    
    try:
        result = api.share(file_id)
        if 'error' in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """上传文件"""
    if 'file' not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400
    
    # 保存文件到临时目录
    filename = secure_filename(file.filename)
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(save_path)
    
    try:
        result = api.upload(save_path)
        # 删除临时文件
        if os.path.exists(save_path):
            os.remove(save_path)
            
        if 'error' in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        return jsonify({"error": str(e)}), 500

@app.route('/api/delete', methods=['POST'])
def delete_file():
    """删除文件"""
    data = request.json
    file_id = data.get('file_id')
    
    if not file_id:
        return jsonify({"error": "缺少file_id参数"}), 400
    
    try:
        result = api.delete(file_id)
        if 'error' in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/create_folder', methods=['POST'])
def create_folder():
    """创建文件夹"""
    data = request.json
    folder_name = data.get('folder_name')
    
    if not folder_name:
        return jsonify({"error": "缺少folder_name参数"}), 400
    
    try:
        result = api.create_folder(folder_name)
        if 'error' in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/reload', methods=['POST'])
def reload_session():
    """重新加载会话"""
    try:
        result = api.reload_session()
        if 'error' in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
