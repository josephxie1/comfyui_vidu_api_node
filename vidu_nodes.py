import os
import time
import json
import requests
import io
from PIL import Image

from comfy.comfy_types import IO
from comfy_api.input_impl import VideoFromFile

# ======================================================================================
# 1. 基础类 (ViduBaseNode)
# ======================================================================================
class ViduBaseNode:
    def __init__(self):
        self.api_base = None
        self.token = None
        self.node_name = self.__class__.__name__
        self._load_api_key()

    def log(self, message: str): print(f"[Vidu::{self.node_name}] {message}")
    def _load_api_key(self):
        try:
            node_dir, config_path = os.path.dirname(os.path.abspath(__file__)), os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api.json')
            self.log(f"正在从 {config_path} 加载API Key...")
            with open(config_path, 'r', encoding='utf-8') as f: config = json.load(f); self.token = config.get("api_key")
            if not self.token: raise ValueError("在 api.json 中找到了文件，但未找到 'api_key' 字段。")
            self.log("API Key 加载成功！")
        except FileNotFoundError: self.log("错误: 未找到 api.json 文件！"); raise FileNotFoundError(f"请在 {os.path.dirname(os.path.abspath(__file__))} 目录下创建 api.json 文件。")
        except json.JSONDecodeError: self.log("错误: api.json 文件格式不正确，无法解析。"); raise ValueError("api.json 文件不是一个有效的JSON。")

    def _make_request(self, method: str, endpoint: str, data: dict = None):
        if not self.token: self._load_api_key()
        if not self.api_base: raise ValueError("API 地址 (api_base) 未在节点中配置")
        headers, url = {"Content-Type": "application/json", "Authorization": f"Token {self.token}"}, f"{self.api_base}{endpoint}"
        self.log(f"发送 {method} 请求到: {url}")
        if data: self.log(f"请求数据: {json.dumps(data, ensure_ascii=False)[:500]}...")
        try:
            response = requests.request(method, url, json=data, headers=headers)
            self.log(f"响应状态码: {response.status_code}")
            if response.status_code != 200: self.log(f"API请求失败: {response.text}"); raise Exception(f"API请求失败 (状态码 {response.status_code}): {response.text}")
            return response.json()
        except requests.RequestException as e: self.log(f"网络请求异常: {e}"); raise Exception(f"网络请求失败: {e}")

    def _upload_image(self, image_tensor) -> str:
        self.log("开始上传图片..."); self.log("步骤 1/3: 请求上传许可...")
        upload_request_data = self._make_request("POST", "/tools/v2/files/uploads", {"scene": "vidu"})
        put_url, resource_id = upload_request_data.get("put_url"), upload_request_data.get("id")
        self.log(f"获取到资源ID: {resource_id}"); self.log("步骤 2/3: 上传图片数据...")
        pil_image = Image.fromarray((image_tensor[0] * 255).cpu().numpy().astype('uint8'))
        img_byte_arr = io.BytesIO(); pil_image.save(img_byte_arr, format='PNG')
        upload_response = requests.put(put_url, data=img_byte_arr.getvalue(), headers={"Content-Type": "image/png"})
        if upload_response.status_code != 200: raise Exception(f"上传图片失败 (状态码 {upload_response.status_code}): {upload_response.text}")
        etag = upload_response.headers.get("etag", "").strip('"')
        if not etag: raise Exception("未能从响应头中获取ETag")
        self.log(f"图片数据上传成功, ETag: {etag}"); self.log("步骤 3/3: 完成上传流程...")
        finish_endpoint = f"/tools/v2/files/uploads/{resource_id}/finish"
        finish_response = self._make_request("PUT", finish_endpoint, data={"etag": etag})
        image_uri = finish_response.get("uri")
        if not image_uri: raise Exception("完成上传后未能获取到图片URI")
        self.log(f"图片上传完成, 获取到URI: {image_uri}"); return image_uri

    def _cancel_task(self, task_id: str):
        self.log(f"正在尝试向Vidu API发送取消请求, 任务ID: {task_id}")
        try:
            cancel_endpoint = f"/ent/v2/tasks/{task_id}/cancel"
            headers, url = {"Content-Type": "application/json", "Authorization": f"Token {self.token}"}, f"{self.api_base}{cancel_endpoint}"
            response = requests.post(url, json={"id": task_id}, headers=headers, timeout=10)
            if response.status_code == 200: self.log(f"✅ 任务 {task_id} 取消请求已成功发送。")
            else: self.log(f"⚠️ 发送取消请求失败 (这可能是因为任务已完成或无法取消): {response.text}")
        except Exception as e: self.log(f"⚠️ 发送取消请求时发生网络错误: {e}")
        
    def _wait_for_completion(self, task_id: str, timeout: int = 3600) -> dict:
        try:
            self.log(f"开始轮询任务状态, ID: {task_id}")
            start_time, query_endpoint = time.time(), f"/ent/v2/tasks/{task_id}/creations"
            while time.time() - start_time < timeout:
                self.log("查询任务状态...")
                status_data = self._make_request("GET", query_endpoint)
                state = status_data.get("state", "未知")
                self.log(f"当前状态: {state}")
                if state == "success": self.log("任务成功完成!"); return status_data
                elif state == "failed": err_code = status_data.get('err_code', 'N/A'); raise Exception(f"任务生成失败，错误码: {err_code}")
                time.sleep(5)
            raise TimeoutError(f"任务轮询超时（超过 {timeout} 秒）")
        except KeyboardInterrupt:
            self.log("!!! 接收到用户中断信号 !!!"); self._cancel_task(task_id); raise

    def _download_video(self, video_url: str, output_path: str, file_prefix: str) -> str:
        if not video_url or not video_url.startswith('http'): raise ValueError(f"无效的video_url: {video_url}")
        self.log(f"开始下载视频: {video_url}"); os.makedirs(output_path, exist_ok=True)
        filename = f"{file_prefix}_{int(time.time())}.mp4"; local_path = os.path.join(output_path, filename)
        self.log(f"将视频保存到: {local_path}")
        with requests.get(video_url, stream=True) as r:
            r.raise_for_status();
            with open(local_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        self.log("视频下载完成!"); return local_path

# ======================================================================================
# NODE IMPLEMENTATIONS
# ======================================================================================

class ViduPromptRecommender(ViduBaseNode):
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"图像": ("IMAGE",), "推荐类型": (["特效和图生视频", "仅特效", "仅图生视频"],),},"optional": {"API地址": ("STRING", {"multiline": False, "default": "https://api.vidu.cn"}), "数量": ("INT", {"default": 5, "min": 1, "max": 10}), "分辨率": (["360p"],),}}
    RETURN_TYPES, RETURN_NAMES, FUNCTION = ("STRING",), ("格式化提示词",), "recommend"
    CATEGORY = "comfyui_VIDU_API" # <--- 已修改

    def recommend(self, **kwargs):
        # ...函数体不变...
        image, recommend_type_cn, self.api_base, count, resolution = kwargs.get("图像"), kwargs.get("推荐类型"), kwargs.get("API地址"), kwargs.get("数量"), kwargs.get("分辨率")
        try:
            self.log(f"开始推荐提示词, 类型: {recommend_type_cn}")
            type_map = {"特效和图生视频": ["template", "img2video"],"仅特效": ["template"],"仅图生视频": ["img2video"],}; api_type = type_map.get(recommend_type_cn)
            image_uri = self._upload_image(image)
            task_data = {"images": [image_uri], "type": api_type, "count": count, "resolution": resolution}
            response_data = self._make_request("POST", "/ent/v2/img2video-prompt-recommendation", task_data)
            prompts = response_data.get("prompts", [])
            if not prompts: return ("未收到任何提示词推荐。",)
            output_lines, template_prompts, img2video_prompts = [], [p for p in prompts if p.get("type") == "template"], [p for p in prompts if p.get("type") == "img2video"]
            if template_prompts:
                output_lines.append("--- 💎 特效模板 (Template Prompts) 💎 ---")
                for i, p in enumerate(template_prompts):
                    output_lines.extend([f"\n[{i+1}] 特效: {p.get('content', 'N/A')}", f"    - 模板名称: {p.get('template', 'N/A')}", f"    - 推荐分辨率: {p.get('resolution', 'N/A')}", f"    - 专用Prompt (可直接复制到生成节点):\n      {p.get('prompt', 'N/A')}"]);
                output_lines.append("\n" + "="*40)
            if img2video_prompts:
                output_lines.append("\n--- 📝 图生视频提示词 (Image to Video Prompts) 📝 ---")
                for i, p in enumerate(img2video_prompts): output_lines.append(f"\n[{i+1}] {p.get('content', 'N/A')}")
            return ("\n".join(output_lines),)
        except Exception as e:
            self.log(f"推荐提示词过程发生错误: {e}"); return (f"错误: {e}",)

class ViduText2VideoNode(ViduBaseNode):
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"提示词": ("STRING", {"multiline": True, "default": "宇航员穿着宇航服在雾中行走，令人印象深刻的全景场面。"}), "模型": (["vidu1.5", "viduq1"],), "风格": (["通用", "动漫"],), "时长(秒)": ([4, 5, 8], {"default": 4}), "分辨率": (["360p", "720p", "1080p"], {"default": "720p"}), "宽高比": (["16:9", "9:16", "1:1"],), "随机种子": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}), "动态幅度": (["自动", "小", "中", "大"],), }, "optional": {"API地址": ("STRING", {"multiline": False, "default": "https://api.vidu.cn"}), "输出路径": ("STRING", {"default": "output"}), "文件名前缀": ("STRING", {"default": "Vidu_Text2Video"}),}}
    RETURN_TYPES, RETURN_NAMES, FUNCTION = (IO.VIDEO, "STRING", "STRING"), ("video", "封面链接", "任务ID"), "generate"
    CATEGORY = "comfyui_VIDU_API" # <--- 已修改
    
    def generate(self, **kwargs):
        # ...函数体不变...
        prompt, model, style_cn, duration, resolution, aspect_ratio, seed, movement_amplitude_cn, self.api_base, output_path, file_prefix = kwargs.get("提示词"), kwargs.get("模型"), kwargs.get("风格"), kwargs.get("时长(秒)"), kwargs.get("分辨率"), kwargs.get("宽高比"), kwargs.get("随机种子"), kwargs.get("动态幅度"), kwargs.get("API地址"), kwargs.get("输出路径"), kwargs.get("文件名前缀")
        try:
            self.log(f"开始文生视频任务, 模型: {model}, 风格: {style_cn}, 提示词: '{prompt[:50]}...'")
            style_map, move_map = {"通用": "general", "动漫": "anime"}, {"自动": "auto", "小": "small", "中": "medium", "大": "large"}
            api_style, api_move = style_map.get(style_cn), move_map.get(movement_amplitude_cn)
            task_data = {"model": model, "style": api_style, "prompt": prompt, "duration": int(duration), "seed": seed, "aspect_ratio": aspect_ratio, "resolution": resolution, "movement_amplitude": api_move}
            task_id = self._make_request("POST", "/ent/v2/text2video", task_data).get("task_id")
            if not task_id: raise Exception("创建任务后未能从响应中获取task_id")
            final_status = self._wait_for_completion(task_id); creations = final_status.get("creations", [])
            if not creations: raise Exception("任务成功，但响应中未找到'creations'结果")
            video_url, cover_url = creations[0].get("url"), creations[0].get("cover_url")
            local_file_path = self._download_video(video_url, output_path, file_prefix)
            self.log(f"任务全部完成! 本地文件路径: {local_file_path}"); video_output = VideoFromFile(local_file_path); return (video_output, cover_url, task_id)
        except Exception as e:
            self.log(f"文生视频过程发生错误: {e}"); return (None, f"错误: {e}", "error")

class ViduImage2VideoNode(ViduBaseNode):
    @classmethod
    def INPUT_TYPES(cls): return {"required": { "图像": ("IMAGE",), "提示词": ("STRING", {"multiline": True, "default": "宇航员挥手，镜头向上移动。"}), "模型": (["vidu2.0", "vidu1.5", "viduq1"],), "时长(秒)": ([4, 5, 8], {"default": 4}), "分辨率": (["360p", "720p", "1080p"], {"default": "720p"}), "随机种子": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}), "动态幅度": (["自动", "小", "中", "大"],), }, "optional": {"API地址": ("STRING", {"multiline": False, "default": "https://api.vidu.cn"}), "输出路径": ("STRING", {"default": "output"}), "文件名前缀": ("STRING", {"default": "Vidu_Image2Video"}),}}
    RETURN_TYPES, RETURN_NAMES, FUNCTION = (IO.VIDEO, "STRING", "STRING"), ("video", "封面链接", "任务ID"), "generate"
    CATEGORY = "comfyui_VIDU_API" # <--- 已修改
    
    def generate(self, **kwargs):
        # ...函数体不变...
        image, prompt, model, duration, resolution, seed, movement_amplitude_cn, self.api_base, output_path, file_prefix = kwargs.get("图像"), kwargs.get("提示词"), kwargs.get("模型"), kwargs.get("时长(秒)"), kwargs.get("分辨率"), kwargs.get("随机种子"), kwargs.get("动态幅度"), kwargs.get("API地址"), kwargs.get("输出路径"), kwargs.get("文件名前缀")
        try:
            self.log(f"开始图生视频任务, 模型: {model}, 提示词: '{prompt[:50]}...'")
            move_map = {"自动": "auto", "小": "small", "中": "medium", "大": "large"}; api_move = move_map.get(movement_amplitude_cn)
            image_uri = self._upload_image(image)
            task_data = {"model": model, "images": [image_uri], "prompt": prompt, "duration": int(duration), "seed": seed, "resolution": resolution, "movement_amplitude": api_move}
            task_id = self._make_request("POST", "/ent/v2/img2video", task_data).get("task_id")
            if not task_id: raise Exception("创建任务后未能从响应中获取task_id")
            final_status = self._wait_for_completion(task_id); creations = final_status.get("creations", [])
            if not creations: raise Exception("任务成功，但响应中未找到'creations'结果")
            video_url, cover_url = creations[0].get("url"), creations[0].get("cover_url")
            local_file_path = self._download_video(video_url, output_path, file_prefix)
            self.log(f"任务全部完成! 本地文件路径: {local_file_path}"); video_output = VideoFromFile(local_file_path); return (video_output, cover_url, task_id)
        except Exception as e:
            self.log(f"图生视频过程发生错误: {e}"); return (None, f"错误: {e}", "error")

class ViduReference2VideoNode(ViduBaseNode):
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"参考图_1": ("IMAGE",),"提示词": ("STRING", {"multiline": True, "default": "一个可爱的角色在奔跑。"}),"模型": (["vidu2.0", "vidu1.5", "viduq1"],),"时长(秒)": ([4, 5, 8], {"default": 4}),"分辨率": (["360p", "720p", "1080p"], {"default": "720p"}),"宽高比": (["16:9", "9:16", "1:1"],),"随机种子": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),"动态幅度": (["自动", "小", "中", "大"],),},"optional": {"API地址": ("STRING", {"multiline": False, "default": "https://api.vidu.cn"}),"参考图_2": ("IMAGE",), "参考图_3": ("IMAGE",), "参考图_4": ("IMAGE",),"参考图_5": ("IMAGE",), "参考图_6": ("IMAGE",), "参考图_7": ("IMAGE",), "输出路径": ("STRING", {"default": "output"}), "文件名前缀": ("STRING", {"default": "Vidu_Reference2Video"}),}}
    RETURN_TYPES, RETURN_NAMES, FUNCTION = (IO.VIDEO, "STRING", "STRING"), ("video", "封面链接", "任务ID"), "generate"
    CATEGORY = "comfyui_VIDU_API" # <--- 已修改
    
    def generate(self, **kwargs):
        # ...函数体不变...
        all_images_cn = [kwargs.get(f"参考图_{i+1}") for i in range(7)]
        prompt, model, duration, resolution, aspect_ratio, seed, movement_amplitude_cn, self.api_base, output_path, file_prefix = kwargs.get("提示词"), kwargs.get("模型"), kwargs.get("时长(秒)"), kwargs.get("分辨率"), kwargs.get("宽高比"), kwargs.get("随机种子"), kwargs.get("动态幅度"), kwargs.get("API地址"), kwargs.get("输出路径"), kwargs.get("文件名前缀")
        try:
            self.log(f"开始参考生视频任务, 模型: {model}, 提示词: '{prompt[:50]}...'")
            move_map = {"自动": "auto", "小": "small", "中": "medium", "大": "large"}; api_move = move_map.get(movement_amplitude_cn)
            image_uris = []
            for i, image_tensor in enumerate(all_images_cn):
                if image_tensor is not None: self.log(f"上传第 {i+1} 张参考图..."); uri = self._upload_image(image_tensor); image_uris.append(uri)
            if not image_uris: raise ValueError("必须至少提供一张参考图片。")
            self.log(f"总共上传了 {len(image_uris)} 张图片。")
            task_data = {"model": model, "images": image_uris, "prompt": prompt, "duration": int(duration), "seed": seed, "aspect_ratio": aspect_ratio, "resolution": resolution, "movement_amplitude": api_move}
            task_id = self._make_request("POST", "/ent/v2/reference2video", task_data).get("task_id")
            if not task_id: raise Exception("创建任务后未能从响应中获取task_id")
            final_status = self._wait_for_completion(task_id); creations = final_status.get("creations", [])
            if not creations: raise Exception("任务成功，但响应中未找到'creations'结果")
            video_url, cover_url = creations[0].get("url"), creations[0].get("cover_url")
            local_file_path = self._download_video(video_url, output_path, file_prefix)
            self.log(f"任务全部完成! 本地文件路径: {local_file_path}"); video_output = VideoFromFile(local_file_path); return (video_output, cover_url, task_id)
        except Exception as e:
            self.log(f"参考生视频过程发生错误: {e}"); return (None, f"错误: {e}", "error")

class ViduStartEnd2VideoNode(ViduBaseNode):
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"起始帧": ("IMAGE",),"结束帧": ("IMAGE",),"模型": (["vidu2.0", "vidu1.5", "viduq1", "viduq1-classic"],),"时长(秒)": ([4, 5, 8], {"default": 4}),"分辨率": (["360p", "720p", "1080p"], {"default": "720p"}),"随机种子": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),"动态幅度": (["自动", "小", "中", "大"],),},"optional": {"API地址": ("STRING", {"multiline": False, "default": "https://api.vidu.cn"}),"提示词": ("STRING", {"multiline": True, "default": ""}), "输出路径": ("STRING", {"default": "output"}), "文件名前缀": ("STRING", {"default": "Vidu_StartEnd2Video"}),}}
    RETURN_TYPES, RETURN_NAMES, FUNCTION = (IO.VIDEO, "STRING", "STRING"), ("video", "封面链接", "任务ID"), "generate"
    CATEGORY = "comfyui_VIDU_API" # <--- 已修改
    
    def generate(self, **kwargs):
        # ...函数体不变...
        start_frame, end_frame, model, duration, resolution, seed, movement_amplitude_cn, self.api_base, prompt, output_path, file_prefix = kwargs.get("起始帧"), kwargs.get("结束帧"), kwargs.get("模型"), kwargs.get("时长(秒)"), kwargs.get("分辨率"), kwargs.get("随机种子"), kwargs.get("动态幅度"), kwargs.get("API地址"), kwargs.get("提示词"), kwargs.get("输出路径"), kwargs.get("文件名前缀")
        try:
            self.log(f"开始首尾帧生视频任务, 模型: {model}"); self.log("注意: 请确保首尾帧图像的分辨率比例在0.8到1.25之间。")
            move_map = {"自动": "auto", "小": "small", "中": "medium", "大": "large"}; api_move = move_map.get(movement_amplitude_cn)
            self.log("上传起始帧图像..."); start_uri = self._upload_image(start_frame)
            self.log("上传结束帧图像..."); end_uri = self._upload_image(end_frame)
            task_data = {"model": model, "images": [start_uri, end_uri], "duration": int(duration), "seed": seed, "resolution": resolution, "movement_amplitude": api_move}
            if prompt and prompt.strip(): task_data["prompt"] = prompt
            task_id = self._make_request("POST", "/ent/v2/start-end2video", task_data).get("task_id")
            if not task_id: raise Exception("创建任务后未能从响应中获取task_id")
            final_status = self._wait_for_completion(task_id); creations = final_status.get("creations", [])
            if not creations: raise Exception("任务成功，但响应中未找到'creations'结果")
            video_url, cover_url = creations[0].get("url"), creations[0].get("cover_url")
            local_file_path = self._download_video(video_url, output_path, file_prefix)
            self.log(f"任务全部完成! 本地文件路径: {local_file_path}"); video_output = VideoFromFile(local_file_path); return (video_output, cover_url, task_id)
        except Exception as e:
            self.log(f"首尾帧生视频过程发生错误: {e}"); return (None, f"错误: {e}", "error")

class ViduFeaturedPresetNode(ViduBaseNode):
    @classmethod
    def INPUT_TYPES(cls): return {"required": {"预设模板": (["outfit_show"],),},"optional": {"API地址": ("STRING", {"multiline": False, "default": "https://api.vidu.cn"}),"提示词": ("STRING", {"multiline": True}),"图像_1": ("IMAGE",),"图像_2": ("IMAGE",),"背景音乐": ("BOOLEAN", {"default": True}),"随机种子": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),"额外JSON参数": ("STRING", {"multiline": True, "default": "{}"}), "输出路径": ("STRING", {"default": "output"}), "文件名前缀": ("STRING", {"default": "Vidu_Preset"}),}}
    RETURN_TYPES, RETURN_NAMES, FUNCTION = (IO.VIDEO, "STRING", "STRING"), ("video", "封面链接", "任务ID"), "generate"
    CATEGORY = "comfyui_VIDU_API" # <--- 已修改
    
    def generate(self, **kwargs):
        # ...函数体不变...
        template_name, self.api_base, prompt, image_1, image_2, bgm, seed, extra_params_json, output_path, file_prefix = kwargs.get("预设模板"), kwargs.get("API地址"), kwargs.get("提示词"), kwargs.get("图像_1"), kwargs.get("图像_2"), kwargs.get("背景音乐"), kwargs.get("随机种子"), kwargs.get("额外JSON参数"), kwargs.get("输出路径"), kwargs.get("文件名前缀")
        try:
            self.log(f"开始特色预设任务, 模板: {template_name}")
            task_data = {"template": template_name}
            if template_name == "outfit_show":
                self.log("处理 'outfit_show' 模板: 需要2张图片和1个提示词。")
                image_uris = []
                if image_1 is not None: self.log("上传图片1..."); image_uris.append(self._upload_image(image_1))
                if image_2 is not None: self.log("上传图片2..."); image_uris.append(self._upload_image(image_2))
                if len(image_uris) < 2: raise ValueError("'outfit_show' 模板需要2张图片。")
                task_data.update({"images": image_uris, "prompt": prompt, "bgm": bgm, "seed": seed})
            try:
                extra_params = json.loads(extra_params_json)
                if extra_params: self.log(f"已合并额外参数: {extra_params}"); task_data.update(extra_params)
            except json.JSONDecodeError: raise ValueError("`额外JSON参数` 格式无效，必须是合法的JSON。")
            task_id = self._make_request("POST", "/ent/v2/template2video", task_data).get("task_id")
            if not task_id: raise Exception("创建任务后未能从响应中获取task_id")
            final_status = self._wait_for_completion(task_id); creations = final_status.get("creations", [])
            if not creations: raise Exception("任务成功，但响应中未找到'creations'结果")
            video_url, cover_url = creations[0].get("url"), creations[0].get("cover_url")
            local_file_path = self._download_video(video_url, output_path, file_prefix)
            self.log(f"任务全部完成! 本地文件路径: {local_file_path}"); video_output = VideoFromFile(local_file_path); return (video_output, cover_url, task_id)
        except Exception as e:
            self.log(f"特色预设过程发生错误: {e}"); return (None, f"错误: {e}", "error")

# ======================================================================================
# REGISTRATION
# ======================================================================================
NODE_CLASS_MAPPINGS = {"ViduPromptRecommender": ViduPromptRecommender,"ViduText2Video": ViduText2VideoNode,"ViduImage2Video": ViduImage2VideoNode,"ViduReference2Video": ViduReference2VideoNode,"ViduStartEnd2Video": ViduStartEnd2VideoNode,"ViduFeaturedPreset": ViduFeaturedPresetNode,}
NODE_DISPLAY_NAME_MAPPINGS = {"ViduPromptRecommender": "comfyui_VIDU_API/推荐提示词","ViduText2Video": "comfyui_VIDU_API/文生视频","ViduImage2Video": "comfyui_VIDU_API/图生视频","ViduReference2Video": "comfyui_VIDU_API/参考生视频","ViduStartEnd2Video": "comfyui_VIDU_API/首尾帧生视频","ViduFeaturedPreset": "comfyui_VIDU_API/特色预设",}