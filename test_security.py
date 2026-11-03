"""Security regressions: the ARCGIS_TOKEN must never reach a non-Esri or non-https host, a title
must not be able to close the generated page's <script> block, and a keyword must not inject XML
into the catalogue query. No network: `_http` is replaced where the code is exercised."""
import json
import os
import pathlib
import re
import tempfile

os.environ.setdefault("ARCGIS_TOKEN", "TESTTOKEN-do-not-use")
os.environ.setdefault("HK_MCP_BIND", "127.0.0.1")
os.environ.setdefault("HK_MCP_DIR", tempfile.mkdtemp(prefix="hk-sec-"))

import hk_spatial_mcp as m


def demo():
    # 1. host allowlist
    for ok in ("https://www.arcgis.com/sharing/rest/search",
               "https://services1.arcgis.com/abc/arcgis/rest/services/x/0/query",
               "https://diag.esri.com/hb"):
        assert m._token_host_ok(ok), f"should allow {ok}"
    for bad in ("https://evil.com/collect", "https://arcgis.com.evil.com/x",
                "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8777/",
                "https://evil.com/arcgis.com", "",
                # a token on plain http is a token in clear text, even to the right host
                "http://www.arcgis.com/sharing/rest/search",
                "http://services1.arcgis.com/abc/query"):
        assert not m._token_host_ok(bad), f"should block {bad}"

    # 2. token never attached to a foreign host
    assert "TESTTOKEN" not in m._with_token("https://evil.com/x")
    assert "TESTTOKEN" in m._with_token("https://services1.arcgis.com/x")

    # 3. arcgis_request refuses a foreign host without making the call
    out = json.loads(m.arcgis_request("https://evil.com/collect", {"q": "x"}))
    assert "error" in out and "Esri" in out["error"], out
    assert "TESTTOKEN" not in json.dumps(out), out

    # 4. the REAL page builder neutralises a title (not just the escaping technique)
    m.WORK = pathlib.Path(tempfile.mkdtemp(prefix="hk-sec-page-"))
    (m.WORK / "data").mkdir(parents=True, exist_ok=True)
    (m.WORK / "data" / "fake.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [114.1, 22.3]},
             "properties": {"NAME": "x"}}]}), encoding="utf-8")
    m.PORT = 0  # only builds the returned url string here; the written page is what we assert on
    built = json.loads(m.build_map(["fake"], "x</script><script>alert(1)</script>"))
    assert built.get("features") == 1, built
    page = (m.WORK / "index.html").read_text(encoding="utf-8")
    assert "</script><script>alert" not in page, "title escaped its script block"
    assert "<\\/script>" in page, "title was not escaped at all"

    # 5. a keyword cannot inject XML into the CSW fallback query
    m._catalog = lambda force=False: []
    seen = {}

    def capture(url, data=None, headers=None, timeout=60):
        seen["body"] = data.decode()
        return b'<?xml version="1.0"?><csw:GetRecordsResponse xmlns:csw="x"/>'

    m._http = capture
    out = json.loads(m.search_hk_data("A&B <c>"))
    lit = re.search(r"<ogc:Literal>(.*?)</ogc:Literal>", seen["body"]).group(1)
    assert lit == "%A&amp;B &lt;c&gt;%", lit
    assert out["matched"] == 0, out

    print("security: 5/5 ok")


if __name__ == "__main__":
    demo()
