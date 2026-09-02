"""
World Tools - WORLD Category
=============================
这些 Tool 只能与外部世界交互，在 Execute 阶段可用。

核心原则：
- 不能操作图（不能创建 Step/Goal/Finding）
- 可以执行命令、发送请求、读写文件
- 负责实际的安全测试操作
"""

from typing import Any, Optional
import subprocess
import requests
from pathlib import Path

from cairn.dispatcher.tools.base import Tool, ToolCategory


class BashTool(Tool):
    """执行 bash 命令

    Execute 阶段用这个来：
    - 运行安全扫描工具
    - 执行代码分析
    - 文件系统操作
    """

    category = ToolCategory.WORLD
    name = "bash"
    description = "Execute a bash command in the container"

    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    async def execute(
        self,
        command: str,
        working_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        """执行命令

        Args:
            command: 要执行的 bash 命令
            working_dir: 可选的工作目录

        Returns:
            包含 stdout, stderr, exit_code 的字典
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=working_dir,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Command timed out after {self.timeout}s",
                "exit_code": -1,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
            }

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要执行的 bash 命令",
                        },
                        "working_dir": {
                            "type": "string",
                            "description": "可选的工作目录路径",
                        },
                    },
                    "required": ["command"],
                },
            },
        }


class HTTPRequestTool(Tool):
    """发送 HTTP 请求

    Execute 阶段用这个来：
    - 测试 API 端点
    - 发送 payload
    - 检查响应
    """

    category = ToolCategory.WORLD
    name = "http_request"
    description = "Send an HTTP request to a URL"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()

    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        body: Optional[str] = None,
        json_body: Optional[dict] = None,
    ) -> dict[str, Any]:
        """发送 HTTP 请求

        Args:
            url: 目标 URL
            method: HTTP 方法（GET/POST/PUT/DELETE/etc）
            headers: 可选的请求头
            body: 可选的请求体（文本）
            json_body: 可选的 JSON 请求体

        Returns:
            包含 status_code, headers, body 的字典
        """
        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                headers=headers,
                data=body,
                json=json_body,
                timeout=self.timeout,
            )
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.text,
                "elapsed_ms": int(response.elapsed.total_seconds() * 1000),
            }
        except Exception as e:
            return {
                "status_code": -1,
                "headers": {},
                "body": "",
                "error": str(e),
            }

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "目标 URL",
                        },
                        "method": {
                            "type": "string",
                            "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                            "description": "HTTP 方法",
                        },
                        "headers": {
                            "type": "object",
                            "description": "请求头字典",
                        },
                        "body": {
                            "type": "string",
                            "description": "请求体文本",
                        },
                        "json_body": {
                            "type": "object",
                            "description": "JSON 请求体",
                        },
                    },
                    "required": ["url"],
                },
            },
        }

    def cleanup(self):
        """清理 HTTP 会话"""
        self.session.close()


class ReadFileTool(Tool):
    """读取文件内容

    Execute 阶段用这个来：
    - 读取源代码
    - 分析配置文件
    - 检查日志
    """

    category = ToolCategory.WORLD
    name = "read_file"
    description = "Read contents of a file"

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir) if base_dir else None

    async def execute(
        self,
        path: str,
        max_lines: Optional[int] = None,
    ) -> dict[str, Any]:
        """读取文件

        Args:
            path: 文件路径
            max_lines: 可选的最大行数限制

        Returns:
            包含 content, line_count 的字典
        """
        try:
            file_path = Path(path)
            if self.base_dir:
                file_path = self.base_dir / file_path

            if not file_path.exists():
                return {
                    "content": "",
                    "line_count": 0,
                    "error": f"File not found: {path}",
                }

            content = file_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()

            if max_lines and len(lines) > max_lines:
                content = "\n".join(lines[:max_lines])
                truncated = True
            else:
                truncated = False

            return {
                "content": content,
                "line_count": len(lines),
                "truncated": truncated,
            }
        except Exception as e:
            return {
                "content": "",
                "line_count": 0,
                "error": str(e),
            }

    def get_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件路径",
                        },
                        "max_lines": {
                            "type": "integer",
                            "description": "可选的最大行数限制",
                        },
                    },
                    "required": ["path"],
                },
            },
        }
