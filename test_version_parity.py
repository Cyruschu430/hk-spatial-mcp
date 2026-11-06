"""Version parity between the two manifests, and the tool-count claims beside them.

The v0.16.0 bump changed `pyproject.toml` but left `.claude-plugin/plugin.json` on 0.15.3, so the
Claude Code plugin advertised a version three releases behind the package that runs. Nothing read
both files, so nothing failed: an installer would simply report the wrong version.

A short file, but it is the only place the two numbers are compared.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "pyproject.toml has no top-level version"
    return m.group(1)


def demo():
    pyp = _pyproject_version()

    # 1. the two manifests agree
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == pyp, (
        f"plugin.json says {plugin['version']}, pyproject.toml says {pyp} — "
        f"bump both or the plugin advertises the wrong release")

    # 2. marketplace.json carries no version of its own to drift
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    for entry in market.get("plugins", []):
        assert "version" not in entry, (
            "marketplace.json declares a per-plugin version; it will drift from plugin.json — "
            "let plugin.json be the single source")

    # 3. every advertised tool count matches the real surface
    src = (ROOT / "hk_spatial_mcp.py").read_text(encoding="utf-8")

    def registered() -> int:
        """The server does not use decorators: `main()` builds a `bim_tools` tuple and a `tools`
        list of function objects, then calls `mcp.tool()(fn)` for each. Parse both literals rather
        than counting a decorator that does not exist."""
        names = set()
        for block in ("bim_tools", "tools"):
            m = re.search(rf"^\s*{block}\s*=\s*[\[(](.*?)[\])]\s*$", src, re.M | re.S)
            assert m, f"could not find the `{block}` registration block"
            # stop at the closing bracket of THIS literal, not the next statement
            body = m.group(1)
            names.update(re.findall(r"\b([a-z][a-z0-9_]{3,})\b", body))
        # keep only names that are really module-level functions, so a typo is caught too
        defined = set(re.findall(r"^def ([a-z][a-z0-9_]*)\(", src, re.M))
        tools = names & defined
        assert names - defined == set(), f"registered but never defined: {sorted(names - defined)}"
        return len(tools)

    n = registered()
    assert n > 20, f"only found {n} registered tools; the parser is probably wrong"

    for name in ("plugin.json", "marketplace.json"):
        text = (ROOT / ".claude-plugin" / name).read_text(encoding="utf-8")
        claimed = {int(x) for x in re.findall(r"\b(\d{2})\s+tools\b", text)}
        for c in claimed:
            assert c == n, f"{name} advertises {c} tools, the server registers {n}"

    # 4. the stdio assertions in the test suite describe the same surface
    for tname in ("test_mcp_client.py", "test_install_path.py"):
        ttext = (ROOT / tname).read_text(encoding="utf-8")
        names = set(re.findall(r'"([a-z0-9_]+)"', ttext))
        # the expected set is a subset of real tool names; compare only that prefix region
        expected = set(re.findall(r'"(bim_[a-z_]+|search_hk_data|load_hk_data|query_hk_data|'
                                 r'summarise_hk_data|build_map|build_scene|geocode_address|'
                                 r'geometry_op|elevation_at|export_static_map|export_static_svg|'
                                 r'arcgis_request|publish_layer|dataset_status|spatial_join|'
                                 r'eo_search|eo_stats|site_zoning|lot_check|bim_findings|'
                                 r'bim_report|publish_findings|publish_scene|webscene_viewer)"', ttext))
        assert len(expected) == n, (
            f"{tname} asserts {len(expected)} tools, the server registers {n} — "
            f"a stale expect-list fails on the next tool added")
        assert names, tname

    print(f"version parity: 4/4 ok ({pyp}, {n} tools)")


if __name__ == "__main__":
    demo()
