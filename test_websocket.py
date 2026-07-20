"""
xiaoyun-lite WebSocket 连接测试
测试 WebSocket 连接、文本聊天和基本功能
"""

import asyncio
import websockets
import json
import sys
import os
import pytest


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_WEBSOCKET_INTEGRATION") != "1",
        reason="需要运行中的 WebSocket 集成环境",
    ),
]

async def test_websocket_connection():
    """测试 WebSocket 连接和基本对话"""
    uri = "ws://localhost:8000/xiaozhi/v1/"
    
    print("=" * 60)
    print("xiaoyun-lite WebSocket 测试")
    print("=" * 60)
    print(f"连接地址: {uri}\n")
    
    try:
        # 连接 WebSocket
        async with websockets.connect(
            uri,
            extra_headers={
                "device-id": "uniapp_test_user",
                "client-id": "test_client_123"
            }
        ) as ws:
            print("✅ WebSocket 连接成功\n")
            
            # 1. 发送 hello 消息
            print("📤 发送 hello 消息...")
            hello_msg = {
                "type": "hello",
                "features": {"mcp": False}
            }
            await ws.send(json.dumps(hello_msg))
            
            # 接收欢迎消息
            try:
                welcome = await asyncio.wait_for(ws.recv(), timeout=5.0)
                print(f"📥 收到欢迎消息: {welcome}\n")
            except asyncio.TimeoutError:
                print("⚠️ 未收到欢迎消息 (可能正常)\n")
            
            # 2. 测试文本聊天
            print("📤 发送聊天消息: '你好,请介绍一下自己'")
            chat_msg = {
                "type": "chat",
                "content": "你好,请介绍一下自己",
                "enable_audio": False
            }
            await ws.send(json.dumps(chat_msg))
            
            # 接收回复
            print("📥 等待回复...\n")
            response = await asyncio.wait_for(ws.recv(), timeout=30.0)
            data = json.loads(response)
            
            print("收到回复:")
            print(f"  类型: {data.get('type')}")
            if 'content' in data:
                print(f"  内容: {data['content']}")
            print()
            
            # 3. 测试画像检索 (如果是 uniapp_ 开头)
            print("📤 测试用户画像功能...")
            chat_msg2 = {
                "type": "chat",
                "content": "推荐一些歌曲给我",
                "enable_audio": False
            }
            await ws.send(json.dumps(chat_msg2))
            
            response2 = await asyncio.wait_for(ws.recv(), timeout=30.0)
            data2 = json.loads(response2)
            
            print("收到画像测试回复:")
            if 'content' in data2:
                print(f"  {data2['content'][:200]}...")
            print()
            
            # 4. 测试多轮对话
            print("📤 测试多轮对话...")
            chat_msg3 = {
                "type": "chat",
                "content": "记住我喜欢听摇滚音乐",
                "enable_audio": False
            }
            await ws.send(json.dumps(chat_msg3))
            
            response3 = await asyncio.wait_for(ws.recv(), timeout=30.0)
            data3 = json.loads(response3)
            print("✅ 多轮对话测试完成\n")
            
            print("=" * 60)
            print("✅ 所有测试通过!")
            print("=" * 60)
            
    except websockets.exceptions.ConnectionRefused:
        print("❌ 连接被拒绝")
        print("请确保:")
        print("  1. xiaoyun-lite 已启动 (python app.py)")
        print("  2. 端口 8000 可用")
        print("  3. 配置文件正确")
        sys.exit(1)
        
    except websockets.exceptions.WebSocketException as e:
        print(f"❌ WebSocket 错误: {e}")
        sys.exit(1)
        
    except asyncio.TimeoutError:
        print("❌ 超时: 服务器响应时间过长")
        print("可能原因:")
        print("  1. LLM API 密钥未配置")
        print("  2. 网络连接问题")
        print("  3. LLM 服务响应慢")
        sys.exit(1)
        
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析错误: {e}")
        sys.exit(1)
        
    except Exception as e:
        print(f"❌ 未知错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    print("\n")
    asyncio.run(test_websocket_connection())
    print("\n")
