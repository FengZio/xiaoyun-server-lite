import os
import yaml
from collections.abc import Mapping
from config.manage_api_client import (
    init_service,
    get_server_config,
    get_agent_models,
    get_agent_config,
    get_device_agent_id,
)


def get_project_dir():
    """获取项目根目录"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"


def read_config(config_path):
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


def load_config():
    """加载配置文件"""
    from core.utils.cache.manager import cache_manager, CacheType

    # 检查缓存
    cached_config = cache_manager.get(CacheType.CONFIG, "main_config")
    if cached_config is not None:
        return cached_config

    default_config_path = get_project_dir() + "config.yaml"
    custom_config_path = get_project_dir() + "data/.config.yaml"

    # 加载默认配置
    default_config = read_config(default_config_path)
    custom_config = read_config(custom_config_path)

    if custom_config.get("manager-api", {}).get("url"):
        config = get_config_from_api(custom_config)
    else:
        # 合并配置
        config = merge_configs(default_config, custom_config)
    # 初始化目录
    ensure_directories(config)

    # 缓存配置
    cache_manager.set(CacheType.CONFIG, "main_config", config)
    return config


def get_config_from_api(config):
    """从Java API获取配置，并合并本地配置中的敏感信息"""
    # 保存本地配置的完整副本（用于提取敏感字段）
    default_config_path = get_project_dir() + "config.yaml"
    default_config = read_config(default_config_path)
    local_config = merge_configs(default_config, config)
    
    # 初始化API客户端
    init_service(config)

    # 获取服务器配置
    config_data = get_server_config()
    if config_data is None:
        raise Exception("Failed to fetch server config from API")

    config_data["read_config_from_api"] = True
    config_data["manager-api"] = {
        "url": config["manager-api"].get("url", ""),
        "secret": config["manager-api"].get("secret", ""),
        "timeout": config["manager-api"].get("timeout", 30),
        "max_retries": config["manager-api"].get("max_retries", 6),
        "retry_delay": config["manager-api"].get("retry_delay", 10),
        # 保留本地 manager-api 数据库配置，供 UniApp 自动创建设备时直连数据库使用。
        "database": config["manager-api"].get("database", {}) or {},
    }
    
    # server的配置以本地为准
    if config.get("server"):
        config_data["server"] = {
            "ip": config["server"].get("ip", ""),
            "port": config["server"].get("port", ""),
            "http_port": config["server"].get("http_port", ""),
            "vision_explain": config["server"].get("vision_explain", ""),
            "auth_key": config["server"].get("auth_key", ""),
        }
    
    # 合并本地配置中的敏感字段到API配置
    # 这样可以保留本地配置的API密钥等敏感信息
    _merge_sensitive_fields(config_data, local_config, "LLM")
    _merge_sensitive_fields(config_data, local_config, "TTS")
    _merge_sensitive_fields(config_data, local_config, "ASR")
    
    return config_data


def _merge_sensitive_fields(api_config, local_config, module_type):
    """
    合并本地配置中的敏感字段到API配置
    
    Args:
        api_config: 从API获取的配置
        local_config: 本地配置（包含敏感信息）
        module_type: 模块类型（LLM、TTS、ASR等）
    """
    # 敏感字段列表（需要从本地配置保留的字段）
    sensitive_fields = [
        "api_key", "access_token", "secret_key", "secret_id",
        "appid", "app_id", "url", "base_url", "api_url",
        "ws_url", "host", "token", "personal_access_token",
        "authorization", "secret", "bot_id", "user_id"
    ]
    
    # 检查模块是否存在
    if module_type not in api_config:
        return
    
    local_module_config = local_config.get(module_type, {})
    if not local_module_config:
        return
    
    # 遍历API配置中的每个子模块
    for module_name, api_module_settings in api_config[module_type].items():
        if not isinstance(api_module_settings, dict):
            continue
        
        # 获取对应的本地模块配置
        local_module_settings = local_module_config.get(module_name, {})
        if not isinstance(local_module_settings, dict):
            continue
        
        # 合并敏感字段
        for field in sensitive_fields:
            if field in local_module_settings:
                local_value = local_module_settings[field]
                
                # 检查本地值是否有效（不是占位符）
                if local_value is not None and isinstance(local_value, str) and "你的" not in local_value and local_value.strip():
                    # 获取API配置中的值
                    api_value = api_module_settings.get(field, "")
                    
                    # 如果API配置中没有该字段，或者值是占位符/空值，则使用本地值
                    if not api_value or "你的" in str(api_value) or not str(api_value).strip():
                        api_module_settings[field] = local_value
                        print(f"从本地配置合并 {module_type}.{module_name} 的 {field} 字段")


def get_private_config_from_api(config, device_id, client_id):
    """从Java API获取私有配置"""
    if device_id and str(device_id).startswith("uniapp_"):
        try:
            agent_id = get_device_agent_id(config, device_id)
            if agent_id:
                agent_config = get_agent_config(config, str(agent_id))
                if agent_config:
                    return agent_config
        except Exception as e:
            print(f"UniApp fallback agent config fetch failed for {device_id}: {e}")

    return get_agent_models(device_id, client_id, config["selected_module"])


def ensure_directories(config):
    """确保所有配置路径存在"""
    dirs_to_create = set()
    project_dir = get_project_dir()  # 获取项目根目录
    # 日志文件目录
    log_dir = config.get("log", {}).get("log_dir", "tmp")
    dirs_to_create.add(os.path.join(project_dir, log_dir))

    # ASR/TTS模块输出目录
    for module in ["ASR", "TTS"]:
        if config.get(module) is None:
            continue
        for provider in config.get(module, {}).values():
            output_dir = provider.get("output_dir", "")
            if output_dir:
                dirs_to_create.add(output_dir)

    # 根据selected_module创建模型目录
    selected_modules = config.get("selected_module", {})
    for module_type in ["ASR", "LLM", "TTS"]:
        selected_provider = selected_modules.get(module_type)
        if not selected_provider:
            continue
        if config.get(module) is None:
            continue
        if config.get(selected_provider) is None:
            continue
        provider_config = config.get(module_type, {}).get(selected_provider, {})
        output_dir = provider_config.get("output_dir")
        if output_dir:
            full_model_dir = os.path.join(project_dir, output_dir)
            dirs_to_create.add(full_model_dir)

    # 统一创建目录（保留原data目录创建）
    for dir_path in dirs_to_create:
        try:
            os.makedirs(dir_path, exist_ok=True)
        except PermissionError:
            print(f"警告：无法创建目录 {dir_path}，请检查写入权限")


def merge_configs(default_config, custom_config):
    """
    递归合并配置，custom_config优先级更高

    Args:
        default_config: 默认配置
        custom_config: 用户自定义配置

    Returns:
        合并后的配置
    """
    if not isinstance(default_config, Mapping) or not isinstance(
        custom_config, Mapping
    ):
        return custom_config

    merged = dict(default_config)

    for key, value in custom_config.items():
        if (
            key in merged
            and isinstance(merged[key], Mapping)
            and isinstance(value, Mapping)
        ):
            merged[key] = merge_configs(merged[key], value)
        else:
            merged[key] = value

    return merged
