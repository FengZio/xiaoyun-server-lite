import os
import time
import base64
from typing import Optional, Dict

import httpx

try:
    import pymysql
except ImportError:
    pymysql = None

TAG = __name__


class DeviceNotFoundException(Exception):
    pass


class DeviceBindException(Exception):
    def __init__(self, bind_code):
        self.bind_code = bind_code
        super().__init__(f"Device binding error, bind code: {bind_code}")


class ManageApiClient:
    _instance = None
    _client = None
    _secret = None

    def __new__(cls, config):
        """Singleton pattern to ensure global unique instance"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._init_client(config)
        return cls._instance

    @classmethod
    def _init_client(cls, config):
        """Initialize persistent connection pool"""
        cls.config = config.get("manager-api")

        if not cls.config:
            raise Exception("manager-api config error")

        if not cls.config.get("url") or not cls.config.get("secret"):
            raise Exception("manager-api url or secret config error")

        if "请" in cls.config.get("secret") or "你" in cls.config.get("secret"):
            raise Exception("Please configure manager-api secret first")

        cls._secret = cls.config.get("secret")
        cls.max_retries = cls.config.get("max_retries", 6)
        cls.retry_delay = cls.config.get("retry_delay", 10)
        
        # Use only ASCII-safe headers to avoid encoding errors
        cls._client = httpx.Client(
            base_url=cls.config.get("url"),
            headers={
                "User-Agent": f"XiaoyunPythonClient/2.0 PID{os.getpid()}",
                "Accept": "application/json",
                "Authorization": "Bearer " + cls._secret,
            },
            timeout=cls.config.get("timeout", 30),
        )

    @classmethod
    def _request(cls, method: str, endpoint: str, **kwargs) -> Dict:
        """Send single HTTP request and handle response"""
        endpoint = endpoint.lstrip("/")
        response = cls._client.request(method, endpoint, **kwargs)
        response.raise_for_status()

        result = response.json()

        # Handle API business errors
        if result.get("code") == 10041:
            raise DeviceNotFoundException(result.get("msg"))
        elif result.get("code") == 10042:
            raise DeviceBindException(result.get("msg"))
        elif result.get("code") != 0:
            raise Exception(f"API error: {result.get('msg', 'Unknown error')}")

        # Return success data
        return result.get("data") if result.get("code") == 0 else None

    @classmethod
    def _should_retry(cls, exception: Exception) -> bool:
        """Determine if exception should be retried"""
        # Network connection errors
        if isinstance(
            exception, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)
        ):
            return True

        # HTTP status code errors
        if isinstance(exception, httpx.HTTPStatusError):
            status_code = exception.response.status_code
            return status_code in [408, 429, 500, 502, 503, 504]

        return False

    @classmethod
    def _execute_request(cls, method: str, endpoint: str, **kwargs) -> Dict:
        """Request executor with retry mechanism"""
        retry_count = 0

        while retry_count <= cls.max_retries:
            try:
                # Execute request
                return cls._request(method, endpoint, **kwargs)
            except Exception as e:
                # Determine if should retry
                if retry_count < cls.max_retries and cls._should_retry(e):
                    retry_count += 1
                    print(
                        f"{method} {endpoint} request failed, retry {retry_count} after {cls.retry_delay:.1f}s"
                    )
                    time.sleep(cls.retry_delay)
                    continue
                else:
                    # Don't retry, raise exception directly
                    raise

    @classmethod
    def safe_close(cls):
        """Safely close connection pool"""
        if cls._client:
            cls._client.close()
            cls._instance = None


def get_server_config() -> Optional[Dict]:
    """Get server base config"""
    return ManageApiClient._instance._execute_request("POST", "/config/server-base")


def get_agent_models(
    mac_address: str, client_id: str, selected_module: Dict
) -> Optional[Dict]:
    """Get agent model config"""
    return ManageApiClient._instance._execute_request(
        "POST",
        "/config/agent-models",
        json={
            "macAddress": mac_address,
            "clientId": client_id,
            "selectedModule": selected_module,
        },
    )


def get_agent_config(config: Dict, agent_id: str) -> Optional[Dict]:
    """
    根据 agent_id 获取智能体配置（供 UniApp 动态切换使用）。
    兼容新版 manager-api 的 /agent/{id}/config 接口。
    """
    if not agent_id:
        return None

    # 确保客户端已初始化
    if ManageApiClient._instance is None:
        init_service(config)

    # 主路径：server 权限接口
    try:
        data = ManageApiClient._instance._execute_request(
            "GET", f"/agent/{agent_id}/config"
        )
        return data
    except Exception:
        # 回退路径：部分旧版本可能使用 /agent/{id}
        try:
            data = ManageApiClient._instance._execute_request(
                "GET", f"/agent/{agent_id}"
            )
            return data
        except Exception as e:
            print(f"Failed to get agent config by agent_id={agent_id}: {e}")
            return None


def save_mem_local_short(mac_address: str, short_momery: str) -> Optional[Dict]:
    try:
        return ManageApiClient._instance._execute_request(
            "PUT",
            f"/agent/saveMemory/" + mac_address,
            json={
                "summaryMemory": short_momery,
            },
        )
    except Exception as e:
        print(f"Failed to save short-term memory to server: {e}")
        return None


def report(
    mac_address: str, session_id: str, chat_type: int, content: str, audio, report_time, agent_id: str = None
) -> Optional[Dict]:
    """Report chat history with circuit breaker"""
    if not content:
        return None
    if not ManageApiClient._instance:
        print("Chat history report skipped: ManageApiClient is not initialized")
        return None
    try:
        request_data = {
            "macAddress": mac_address,
            "sessionId": session_id,
            "chatType": chat_type,
            "content": content,
            "reportTime": report_time,
            "audioBase64": (
                base64.b64encode(audio).decode("utf-8") if audio else None
            ),
        }
        # Add agent_id if provided
        if agent_id:
            request_data["agentId"] = agent_id

        print(
            "Chat history report request: "
            f"mac={mac_address}, session={session_id}, chatType={chat_type}, "
            f"agent={agent_id}, content_len={len(content)}, has_audio={bool(audio)}"
        )
        result = ManageApiClient._instance._execute_request(
            "POST",
            f"/agent/chat-history/report",
            json=request_data,
        )
        print(f"Chat history report response data: {result}")
        return result
    except Exception as e:
        print(f"Chat history report failed: {e}")
        return None


def init_service(config):
    """Initialize ManageApiClient"""
    ManageApiClient(config)


def manage_api_http_safe_close():
    """Close HTTP client"""
    ManageApiClient.safe_close()


def create_uniapp_device_if_not_exists(config, device_id, user_id):
    """
    Create UniApp device if it does not exist.

    Returns:
        dict: {
            "exists": bool,
            "created": bool,
            "agent_id": str,
            "device_name": str,
        }
    """
    if pymysql is None:
        raise Exception("pymysql is required: pip install pymysql")

    manager_api_config = config.get("manager-api", {})
    manager_api_url = manager_api_config.get("url", "http://localhost:8002")
    if not manager_api_url:
        raise Exception("manager-api url is not configured")

    # 1) Check existence via API if client is initialized
    try:
        if ManageApiClient._instance:
            try:
                result = get_agent_models(device_id, device_id, {})
                if result:
                    return {
                        "exists": True,
                        "created": False,
                        "agent_id": result.get("agentId"),
                        "device_name": result.get(
                            "deviceName", f"UniApp-User-{user_id}"
                        ),
                    }
            except DeviceNotFoundException:
                pass
            except Exception:
                pass
    except Exception:
        pass

    # 2) Create device directly in DB
    agent_id = _get_default_agent_id(config)
    if not agent_id:
        raise Exception("default agent_id not found")

    db_config = _get_database_config(config)
    device_name = f"UniApp-User-{user_id}"

    try:
        connection = pymysql.connect(**db_config)
        try:
            with connection.cursor() as cursor:
                check_sql = "SELECT id, agent_id FROM ai_device WHERE mac_address = %s"
                cursor.execute(check_sql, (device_id,))
                result = cursor.fetchone()

                if result:
                    agent_value = None
                    if isinstance(result, dict):
                        agent_value = result.get("agent_id") or agent_id
                    elif isinstance(result, (list, tuple)) and len(result) > 1:
                        agent_value = result[1]
                    return {
                        "exists": True,
                        "created": False,
                        "agent_id": agent_value or agent_id,
                        "device_name": device_name,
                    }

                import uuid

                device_uuid = uuid.uuid4().hex

                user_id_value = None
                include_user_id = False
                if user_id and str(user_id).isdigit():
                    try:
                        user_id_value = int(user_id)
                        include_user_id = True
                    except (ValueError, TypeError):
                        pass

                if include_user_id:
                    insert_sql = """
                        INSERT INTO ai_device (
                            id, mac_address, alias, device_type,
                            user_id, agent_id, create_date, update_date
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, NOW(), NOW()
                        )
                    """
                    cursor.execute(
                        insert_sql,
                        (
                            device_uuid,
                            device_id,
                            device_name,
                            "uniapp",
                            user_id_value,
                            agent_id,
                        ),
                    )
                else:
                    insert_sql = """
                        INSERT INTO ai_device (
                            id, mac_address, alias, device_type,
                            agent_id, create_date, update_date
                        ) VALUES (
                            %s, %s, %s, %s, %s, NOW(), NOW()
                        )
                    """
                    cursor.execute(
                        insert_sql,
                        (
                            device_uuid,
                            device_id,
                            device_name,
                            "uniapp",
                            agent_id,
                        ),
                    )
                connection.commit()

                return {
                    "exists": False,
                    "created": True,
                    "agent_id": agent_id,
                    "device_name": device_name,
                }
        finally:
            connection.close()
    except Exception as e:
        raise Exception(f"create device failed: {e}")


def _get_default_agent_id(config):
    default_agent_id = config.get("default_uniapp_agent_id")
    if default_agent_id:
        return default_agent_id

    try:
        if pymysql is None:
            raise ImportError("pymysql not installed")

        db_config = _get_database_config(config)
        connection = pymysql.connect(**db_config)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM ai_agent LIMIT 1")
                result = cursor.fetchone()
                if isinstance(result, dict) and result.get("id"):
                    return result["id"]
                if isinstance(result, (list, tuple)) and len(result) > 0:
                    return result[0]
        finally:
            connection.close()
    except Exception:
        pass

    return "1b1b0c33891e4afc9e2406ac042948c2"


def _get_database_config(config):
    manager_api_config = config.get("manager-api", {})
    manager_api_url = manager_api_config.get("url", "http://localhost:8002")

    if "://" in manager_api_url:
        host = manager_api_url.split("://")[1].split(":")[0]
    else:
        host = manager_api_url.split(":")[0]

    if host in ["localhost", "127.0.0.1", "xiaozhi-esp32-server-web"]:
        host = "xiaozhi-esp32-server-db"

    db_config = {
        "host": host,
        "port": 3306,
        "user": "root",
        "password": "123456",
        "database": "xiaozhi_esp32_server",
        "charset": "utf8mb4",
    }

    if pymysql is not None:
        db_config["cursorclass"] = pymysql.cursors.DictCursor

    return db_config
