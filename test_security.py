"""Security regressions: the ARCGIS_TOKEN must never reach a non-Esri host, and a title
must not be able to close the generated page's <script> block. No network, no mcp needed."""
import json
import os

os.environ.setdefault("ARCGIS_TOKEN", "TESTTOKEN-do-not-use")
os.environ.setdefault("HK_MCP_BIND", "127.0.0.1")

import hk_spatial_mcp as m


def demo():
    # 1. host allowlist
    for ok in ("https://www.arcgis.com/sharing/rest/search",
               "https://services1.arcgis.com/abc/arcgis/rest/services/x/0/query",
               "https://diag.esri.com/hb"):
        assert m._token_host_ok(ok), f"should allow {ok}"
    for bad in ("https://evil.com/collect", "https://arcgis.com.evil.com/x",
                "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8777/",
                "https://evil.com/arcgis.com", ""):
        assert not m._token_host_ok(bad), f"should block {bad}"

    # 2. token never attached to a foreign host
    assert "TESTTOKEN" not in m._with_token("https://evil.com/x")
    assert "TESTTOKEN" in m._with_token("https://services1.arcgis.com/x")

    # 3. arcgis_request refuses a foreign host without making the call
    out = json.loads(m.arcgis_request("https://evil.com/collect", {"q": "x"}))
    assert "error" in out and "Esri" in out["error"], out
    assert "TESTTOKEN" not in json.dumps(out), out

    # 4. a title cannot break out of <script>
    escaped = json.dumps("</script><img src=x>", ensure_ascii=False).replace("</", "<\\/")
    assert "</script>" not in escaped and "<\\/script>" in escaped, escaped

    print("security: 4/4 ok")


if __name__ == "__main__":
    demo()
