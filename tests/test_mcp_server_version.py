"""serverInfo names Quorus's version, not the mcp library's (0.5.0 said 1.30.0)."""

from quorus_mcp import server

import quorus


def test_mcp_server_info_reports_the_quorus_version() -> None:
    opts = server.mcp._mcp_server.create_initialization_options()
    assert opts.server_name == "quorus"
    assert opts.server_version == quorus.__version__
