# 打印一个清晰的加载提示，方便在启动ComfyUI时确认节点包是否被加载
print("Initializing comfyui_vidu_api nodes...")

# 从您的主py文件中导入我们创建的所有节点类
from .vidu_nodes import (
    ViduPromptRecommender, # 新增
    ViduText2VideoNode,
    ViduImage2VideoNode,
    ViduReference2VideoNode,
    ViduStartEnd2VideoNode,
    ViduFeaturedPresetNode,
)

# 定义一个字典，将节点的内部名称映射到它们的类
NODE_CLASS_MAPPINGS = {
    "ViduPromptRecommender": ViduPromptRecommender, # 新增
    "ViduText2Video": ViduText2VideoNode,
    "ViduImage2Video": ViduImage2VideoNode,
    "ViduReference2Video": ViduReference2VideoNode,
    "ViduStartEnd2Video": ViduStartEnd2VideoNode,
    "ViduFeaturedPreset": ViduFeaturedPresetNode,
}

# 定义显示名称，创建子菜单
NODE_DISPLAY_NAME_MAPPINGS = {
    "ViduPromptRecommender": "comfyui_vidu_api/推荐提示词", # 新增
    "ViduText2Video": "comfyui_vidu_api/文生视频",
    "ViduImage2Video": "comfyui_vidu_api/图生视频",
    "ViduReference2Video": "comfyui_vidu_api/参考生视频",
    "ViduStartEnd2Video": "comfyui_vidu_api/首尾帧生视频",
    "ViduFeaturedPreset": "comfyui_vidu_api/特色预设",
}

# 打印加载成功的信息
print("✅ comfyui_vidu_api nodes loaded successfully!")

# 这是Python模块的标准部分，确保ComfyUI可以正确地导入上面的两个字典
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']