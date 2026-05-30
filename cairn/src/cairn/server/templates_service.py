"""Built-in project templates for the Template Engine.

This module is intentionally a standalone module (``templates_service.py``)
rather than a ``services/templates.py`` package member. The existing
``cairn.server.services`` is a single module (``services.py``) imported across
the server (``from cairn.server.services import ...``); introducing a
``services/`` package would shadow that module and break those core imports. So
this additive service lives alongside ``vulnerability_extraction.py`` and
``templates_models.py`` instead, mirroring the convention already established in
this package.

Built-in templates are defined as an in-memory Python constant rather than being
stored in the database (see design.md, "Template Engine" / "Built-in
Templates"). Only user-created custom templates are persisted (in the
``templates`` table created by the product schema). The templates router merges
this constant list with a user's stored custom templates when answering
``GET /api/templates``.

Each built-in template satisfies requirements 12.1 and 12.2:

* 12.1 -- at minimum the four templates Web Application Assessment, Internal
  Network Pentest, External Network Pentest, and CTF Challenge are provided.
* 12.2 -- each template carries a title, an ``origin`` fact, a ``goal`` fact, and
  between 1 and 10 initial ``hints`` where every hint is a ``{content, creator}``
  mapping whose ``creator`` is ``"template"``.

The shapes mirror :class:`cairn.server.templates_models.TemplateResponse`:
built-in templates use ``is_builtin=True`` and leave ``user_id`` as ``None``.
"""

from __future__ import annotations

# Marker stored in every built-in hint's ``creator`` field (requirement 12.2).
TEMPLATE_CREATOR = "template"


def _hint(content: str) -> dict[str, str]:
    """Build a ``{content, creator}`` hint with the template creator marker."""
    return {"content": content, "creator": TEMPLATE_CREATOR}


# The built-in templates surfaced by the Template Engine. Each entry mirrors the
# ``TemplateResponse`` shape (``is_builtin=True``, ``user_id=None``) so the
# templates router can return built-in and custom templates through one model.
BUILTIN_TEMPLATES: list[dict] = [
    {
        "id": "builtin-web-app",
        "title": "Web 应用评估",
        "origin": "目标 Web 应用，地址为 [URL]，技术栈为 [technology stack]",
        "goal": "识别并记录 Web 应用中所有可利用的漏洞",
        "hints": [
            _hint(
                "从侦察开始：枚举子域名、目录和所用技术"
            ),
            _hint("测试身份认证机制是否存在绕过漏洞"),
            _hint("检查所有用户输入是否存在注入点"),
        ],
        "is_builtin": True,
        "user_id": None,
    },
    {
        "id": "builtin-internal-network",
        "title": "内网渗透测试",
        "origin": "可从假定已突破的立足点访问的内网网段 [CIDR]",
        "goal": "实现域控制权获取，并记录横向移动和权限提升路径",
        "hints": [
            _hint(
                "枚举内网网段中的存活主机和服务"
            ),
            _hint(
                "识别 Active Directory 资产：域控制器、用户和用户组"
            ),
            _hint(
                "在共享、脚本和服务配置中搜寻凭据"
            ),
            _hint(
                "梳理通往域管理员的横向移动和权限提升路径"
            ),
        ],
        "is_builtin": True,
        "user_id": None,
    },
    {
        "id": "builtin-external-network",
        "title": "外网渗透测试",
        "origin": "[organization] 面向互联网的资产：在范围内的 IP 段和域名",
        "goal": "发现并利用对外暴露的服务，以获得初始立足点",
        "hints": [
            _hint(
                "枚举外部攻击面：开放端口、服务和暴露的应用"
            ),
            _hint(
                "识别服务版本指纹，并检查是否存在已知可利用的漏洞"
            ),
            _hint(
                "测试暴露的登录入口是否使用弱口令或默认凭据"
            ),
            _hint(
                "检查 TLS 配置和证书是否存在错误配置"
            ),
        ],
        "is_builtin": True,
        "user_id": None,
    },
    {
        "id": "builtin-ctf",
        "title": "CTF 挑战",
        "origin": "CTF 挑战目标，地址为 [host:port]，类别为 [web/pwn/crypto/forensics]",
        "goal": "解出挑战并取得 flag",
        "hints": [
            _hint("确定挑战类别，并收集所有提供的文件或接口"),
            _hint("针对该类别探测预期的漏洞类型"),
            _hint("开发漏洞利用或解题方案并夺取 flag"),
        ],
        "is_builtin": True,
        "user_id": None,
    },
]
