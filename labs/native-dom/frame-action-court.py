#!/usr/bin/env python3
"""The court for frame-aware actions and child-local navigation.

Frozen from `frame-action-design-0.0.1.md` §21 and §23.4 before the host
changed. Strictly headless: no surface binary, no window, no AppKit, and it
refuses to run with the visible-court variable set. One hermetic loopback
origin, both allocators, a fresh host per run.

Groups:
 1 identity     an action in a child changes only that child
 2 revision     every event of §14.1 moves R by exactly its amount
 3 stale        an action anywhere stales every reference, band reuse included
 4 isolation    one frame's snapshot never authorises another frame's node
 5 wait         a wait converges on a revision a child's action reached
 6 navigation   a child link and a child GET submit replace that child only
 7 rollback     a failed child navigation leaves identity, document and R alone
 8 budget       a child navigation spends the parent document's allowance
 9 audit        every action record names its frame and no page text
10 teardown     a parent navigation still ends every child, R correct after
11 closed       sandbox, targets, download, javascript and fragments fail closed
12 handlers     the main frame's multi-step handler revision is preserved
"""

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "protocol"))
import check_contract  # noqa: E402

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = [name]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = saved
    return module


RETENTION = load_module("retention_court", Path(__file__).with_name("retention-court.py"))


def page(title, bodies):
    return ("<!doctype html><html><body><main><h1>" + title + "</h1>"
            + "".join(bodies) + "</main></body></html>").encode()


LINKS = [
    ('<a id="plain" href="/landed.html">plain</a>', "allowed"),
    ('<a id="self" href="/landed.html" target="_self">self</a>', "allowed"),
    ('<a id="empty" href="/landed.html" target="">empty</a>', "allowed"),
    ('<a id="upper" href="/landed.html" target="_SELF">upper</a>', "allowed"),
    ('<a id="parent" href="/landed.html" target="_parent">parent</a>', None),
    ('<a id="top" href="/landed.html" target="_Top">top</a>', None),
    ('<a id="blank" href="/landed.html" target="_blank">blank</a>', "target_named"),
    ('<a id="named" href="/landed.html" target="side">named</a>', "target_named"),
    ('<a id="down" href="/landed.html" download>down</a>', "download_unsupported"),
    ('<a id="js" href="javascript:void(0)">js</a>', "scheme_unsupported"),
    ('<a id="frag" href="#here">frag</a>', "fragment_unsupported"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    if os.environ.get(VISIBLE_ENV):
        print(json.dumps({"passed": False, "reason": "the visible-court variable is set"}))
        return 1

    network = RETENTION.load_network_module()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            targets = "".join(html for html, _ in LINKS)
            pages = {
                "/parent.html": page("Parent", ['<iframe src="/child.html"></iframe>',
                                                '<p id="pm">parent alpha</p>']),
                "/child.html": page("Child", [
                    '<p id="cp">child alpha</p>',
                    '<a id="cl" href="/child-landed.html">Go</a>',
                    '<form id="cf" method="get" action="/child-landed.html">'
                    '<input id="ci" name="q" type="text" value="">'
                    '<button id="cb" type="submit">Send</button></form>',
                    '<a id="cmiss" href="/absent.html">Missing</a>']),
                # The landing page offers a way back, so a child can be
                # navigated until the parent document's allowance runs out.
                "/child-landed.html": page("Child landed", [
                    '<p id="cd">child landed</p>',
                    '<a id="back" href="/child.html">Back</a>']),
                "/landed.html": page("Landed", ['<p id="lp">landed</p>']),
                "/targets.html": page("Targets", [targets]),
                "/parent-targets.html": page("Parent targets", ['<iframe src="/targets.html"></iframe>']),
                "/base-target.html": page("Base target", [
                    '<base target="_blank">',
                    '<a id="nobase" href="/landed.html">no target of its own</a>',
                    '<a id="own" href="/landed.html" target="_self">its own</a>']),
                "/forms.html": page("Forms", [
                    '<form id="get" method="get" action="/landed.html">'
                    '<button id="plain" type="submit">plain</button>'
                    '<button id="fmpost" type="submit" formmethod="POST">post</button>'
                    '<button id="ftblank" type="submit" formtarget="_blank">blank</button>'
                    '<button id="ftself" type="submit" formtarget="_SELF">self</button>'
                    '<button id="faction" type="submit" formaction="/child-landed.html">action</button>'
                    '</form>',
                    '<form id="post" method="POST" action="/landed.html">'
                    '<button id="pplain" type="submit">plain</button>'
                    '<button id="fmget" type="submit" formmethod="get">get</button></form>',
                    '<form id="blankform" method="get" action="/landed.html" target="_blank">'
                    '<button id="bplain" type="submit">plain</button></form>']),
                "/handler.html": page("Handler", [
                    '<form id="hf" method="get" action="/absent.html">'
                    '<input id="ht" name="t" type="text" value="">'
                    '<button id="hb" type="submit">Send</button></form>',
                    '<p id="m1">one</p><p id="m2">two</p>',
                    '<script>document.getElementById("hf").addEventListener("submit",function(){'
                    'document.getElementById("m1").textContent="handler one";'
                    'document.getElementById("m2").textContent="handler two";});</script>']),
                "/quiet.html": page("Quiet", ['<a id="q" href="/landed.html">quiet</a>',
                                              '<input id="qi" name="q" type="text" value="">']),
            }
            if path in pages:
                return self.reply(200, pages[path])
            if path == "/absent.html":
                return self.reply(404, b"gone")
            return super().do_GET()

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    responses = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    def refused(response, code, reason=None):
        error = response.get("error") or {}
        if response.get("ok") or error.get("code") != code:
            return False
        return reason is None or (error.get("details") or {}).get("reason") == reason

    try:
        for allocator in ("system", "arena"):
            with tempfile.TemporaryDirectory(prefix="minicon-surf-frame-action-") as directory:
                host = RETENTION.Host(args.binary, directory, origin, allocator)
                tag = f"[{allocator}] "

                def call(operation, arguments, version="0.0.2", deadline_ms=30000):
                    request = {"protocol": "minicon-surf.control", "version": version,
                               "request_id": "req_fa_0", "deadline_ms": deadline_ms,
                               "operation": operation, "arguments": arguments}
                    check_contract.validate_request(request)
                    host.counter += 1
                    request["request_id"] = f"req_fa_{host.counter}"
                    host.process.stdin.write(json.dumps(request) + "\n")
                    host.process.stdin.flush()
                    line = host.process.stdout.readline()
                    if not line:
                        raise RuntimeError(f"host exited during {operation}")
                    response = json.loads(line)
                    check_contract.validate_response(response)
                    responses.append(json.dumps(response))
                    return response

                def ok(operation, arguments, **kw):
                    response = call(operation, arguments, **kw)
                    if not response["ok"]:
                        raise RuntimeError(f"{operation} failed: {response['error']}")
                    return response["result"]

                def open_page(session, path):
                    return ok("target.open", {"session": session, "url": f"{origin}{path}"})["target"]

                def snap(target, **extra):
                    return call("target.snapshot", {"target": target, "format": "semantic",
                                                    "max_bytes": 65536, "max_nodes": 64, **extra})

                def nodes_of(answer):
                    return answer["result"]["nodes"] if answer.get("ok") else []

                def find(answer, role, name=None, dom_id=None):
                    for node in nodes_of(answer):
                        if node.get("role") != role:
                            continue
                        if dom_id is not None and node.get("dom_id") != dom_id:
                            continue
                        if name is not None and name not in (node.get("name") or ""):
                            continue
                        return node
                    return None

                def revision(target):
                    return ok("target.inspect", {"target": target})["revision"]

                def frames(target):
                    return ok("target.inspect", {"target": target})["frames"]

                try:
                    profile = ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = ok("session.open", {"profile": profile})["session"]

                    # 1 and 2. Identity, and the revision of a child mutation.
                    target = open_page(session, "/parent.html")
                    before = ok("target.inspect", {"target": target})
                    child = before["frames"][1]["frame"]
                    child_snap = snap(target, frame=child)
                    box = find(child_snap, "textbox")
                    r0 = revision(target)
                    acted = call("target.act", {"target": target, "reference": box["reference"],
                                                "action": {"kind": "set_value", "value": "court-fake"}})
                    after = ok("target.inspect", {"target": target})
                    expect(tag + "an action in a child is served and advances the revision by exactly one",
                           acted.get("ok") and acted["result"]["applied"] is True
                           and after["revision"] == r0 + 1,
                           {"revision": [r0, after["revision"]]})
                    expect(tag + "it changes only that child: the parent's identity and history stand",
                           after["url"] == before["url"]
                           and after["frames"][0] == before["frames"][0]
                           and after["history"] == before["history"]
                           and len(after["frames"]) == 2
                           and after["frames"][1]["frame"] == child
                           and after["frames"][1]["realm"] == before["frames"][1]["realm"],
                           {"frames": len(after["frames"])})

                    # 3. Staleness reaches every frame.
                    main_snap = snap(target, frame=before["frames"][0]["frame"])
                    main_node = find(main_snap, "text")
                    r1 = revision(target)
                    child_snap = snap(target, frame=child)
                    box = find(child_snap, "textbox")
                    call("target.act", {"target": target, "reference": box["reference"],
                                        "action": {"kind": "set_value", "value": "court-fake-two"}})
                    stale = call("target.act", {"target": target, "reference": main_node["reference"],
                                                "action": {"kind": "click"}})
                    expect(tag + "an action in a child stales a reference held in the main frame",
                           refused(stale, "stale_revision"), {"error": stale.get("error")})

                    # 4. One frame's snapshot never authorises another's node.
                    main_snap = snap(target, frame=before["frames"][0]["frame"])
                    main_ref = find(main_snap, "text")["reference"]
                    borrowed = dict(main_ref)
                    borrowed["node"] = f"node_{128 + 1}"
                    answer = call("target.act", {"target": target, "reference": borrowed,
                                                 "action": {"kind": "click"}})
                    expect(tag + "a main-frame observation cannot authorise a node in a child's band",
                           refused(answer, "not_found"), {"error": answer.get("error")})
                    unseen = dict(main_ref)
                    unseen["node"] = f"node_{128 * 7 + 1}"
                    answer = call("target.act", {"target": target, "reference": unseen,
                                                 "action": {"kind": "click"}})
                    expect(tag + "a band with no live frame behind it is the same not_found",
                           refused(answer, "not_found"), {"error": answer.get("error")})

                    # 5. A wait converges on what a child's action reached.
                    child_snap = snap(target, frame=child)
                    box = find(child_snap, "textbox")
                    reached = revision(target) + 1
                    call("target.act", {"target": target, "reference": box["reference"],
                                        "action": {"kind": "set_value", "value": "court-fake-three"}})
                    waited = call("target.wait", {"target": target,
                                                  "condition": {"kind": "revision_at_least",
                                                                "revision": reached}}, deadline_ms=5000)
                    expect(tag + "a wait converges on a revision a child's action reached",
                           waited.get("ok") and waited["result"]["matched"] is True
                           and waited["result"]["revision"] >= reached,
                           {"revision": waited.get("result", {}).get("revision")})

                    # 6. A child link replaces that child's document only.
                    state = ok("target.inspect", {"target": target})
                    child_snap = snap(target, frame=child)
                    link = find(child_snap, "link")
                    r2 = revision(target)
                    navigated = call("target.act", {"target": target, "reference": link["reference"],
                                                    "action": {"kind": "click"}})
                    moved = ok("target.inspect", {"target": target})
                    expect(tag + "a link in a child replaces that child's document and nothing else",
                           navigated.get("ok") and navigated["result"].get("navigated") is True
                           and navigated["result"]["frame"] == child
                           and moved["frames"][1]["generation"] == state["frames"][1]["generation"] + 1
                           and moved["frames"][1]["realm"] != state["frames"][1]["realm"]
                           and moved["frames"][1]["frame"] == child
                           and str(moved["frames"][1].get("url", "")).endswith("/child-landed.html")
                           and moved["revision"] == r2 + 1
                           and moved["url"] == state["url"]
                           and moved["frames"][0] == state["frames"][0]
                           and moved["history"] == state["history"],
                           {"revision": [r2, moved["revision"]],
                            "generation": moved["frames"][1]["generation"]})
                    # A reference from the replaced document does not survive,
                    # even though its band belongs to the same child.
                    answer = call("target.act", {"target": target, "reference": link["reference"],
                                                 "action": {"kind": "click"}})
                    expect(tag + "a reference from the replaced child document is refused, band reuse included",
                           refused(answer, "stale_revision") or refused(answer, "not_found"),
                           {"error": answer.get("error")})
                    ok("target.close", {"target": target})

                    # 6b. A GET submit in a child does the same.
                    target = open_page(session, "/parent.html")
                    state = ok("target.inspect", {"target": target})
                    child = state["frames"][1]["frame"]
                    child_snap = snap(target, frame=child)
                    form = find(child_snap, "form")
                    r3 = revision(target)
                    submitted = call("target.act", {"target": target, "reference": form["reference"],
                                                    "action": {"kind": "submit"}})
                    moved = ok("target.inspect", {"target": target})
                    expect(tag + "a GET submit in a child navigates that child with its query",
                           submitted.get("ok") and submitted["result"].get("navigated") is True
                           and "?q=" in str(moved["frames"][1].get("url", ""))
                           and moved["frames"][1]["frame"] == child
                           and moved["revision"] == r3 + 1
                           and moved["url"] == state["url"]
                           and moved["history"] == state["history"],
                           {"revision": [r3, moved["revision"]]})

                    # 7. A failed child navigation leaves everything alone.
                    ok("target.close", {"target": target})
                    target = open_page(session, "/parent.html")
                    child = frames(target)[1]["frame"]
                    state = ok("target.inspect", {"target": target})
                    child_snap = snap(target, frame=child)
                    missing = find(child_snap, "link", dom_id="cmiss")
                    failed = call("target.act", {"target": target, "reference": missing["reference"],
                                                 "action": {"kind": "click"}}) if missing else {}
                    unchanged = ok("target.inspect", {"target": target})
                    expect(tag + "a failed child navigation keeps the child's identity, document and the revision",
                           (missing is None or not failed.get("ok"))
                           and unchanged["frames"][1] == state["frames"][1]
                           and unchanged["revision"] == state["revision"],
                           {"error": failed.get("error"), "revision": unchanged["revision"]})
                    ok("target.close", {"target": target})

                    # 8. The parent document's allowance is what a child spends.
                    target = open_page(session, "/parent.html")
                    child = frames(target)[1]["frame"]
                    spent = None
                    for _ in range(40):
                        child_snap = snap(target, frame=child)
                        link = find(child_snap, "link")
                        if link is None:
                            form = find(child_snap, "form")
                            if form is None:
                                break
                            answer = call("target.act", {"target": target, "reference": form["reference"],
                                                         "action": {"kind": "submit"}})
                        else:
                            answer = call("target.act", {"target": target, "reference": link["reference"],
                                                         "action": {"kind": "click"}})
                        if not answer.get("ok"):
                            spent = answer
                            break
                    expect(tag + "a child navigation spends the parent document's aggregate allowance",
                           spent is not None and refused(spent, "resource_limit"),
                           {"error": (spent or {}).get("error")})
                    ok("target.navigate", {"target": target, "url": f"{origin}/parent.html"})
                    reset = ok("target.inspect", {"target": target})
                    expect(tag + "replacing the parent document resets the allowance for its new frames",
                           reset["network"]["fetches"] <= 2 and len(reset["frames"]) == 2,
                           {"fetches": reset["network"]["fetches"]})

                    # 10. Teardown, with the revision correct afterwards.
                    state = ok("target.inspect", {"target": target})
                    child = state["frames"][1]["frame"]
                    child_snap = snap(target, frame=child)
                    box = find(child_snap, "textbox")
                    call("target.act", {"target": target, "reference": box["reference"],
                                        "action": {"kind": "set_value", "value": "court-fake-four"}})
                    before_nav = revision(target)
                    ok("target.navigate", {"target": target, "url": f"{origin}/parent.html"})
                    after_nav = ok("target.inspect", {"target": target})
                    expect(tag + "a parent navigation ends its children and advances the revision by one",
                           after_nav["revision"] == before_nav + 1
                           and len(after_nav["frames"]) == 2
                           and after_nav["frames"][1]["frame"] != child,
                           {"revision": [before_nav, after_nav["revision"]]})
                    gone = snap(target, frame=child)
                    expect(tag + "the ended child is the same not_found afterwards",
                           refused(gone, "not_found"), {"error": gone.get("error")})

                    # 9. The audit names the frame and no page text.
                    audit = ok("session.inspect", {"session": session})
                    entries = (audit.get("audit") or {}).get("entries") or []
                    acts = [e for e in entries if str(e.get("operation", "")).startswith("target.act")]
                    blob = json.dumps(entries)
                    expect(tag + "every action record names the frame it touched",
                           acts and all(e.get("frame") for e in acts)
                           and any(e["frame"] != acts[0]["target"] for e in acts),
                           {"records": len(acts)})
                    expect(tag + "no URL, value, target name or other page text is in the ledger",
                           "court-fake" not in blob and "child-landed" not in blob
                           and "_blank" not in blob and "side" not in blob,
                           {"bytes": len(blob)})
                    ok("target.close", {"target": target})

                    # 11. The closed vocabulary, in the main frame and in a child.
                    for page_path, frame_kind in (("/targets.html", "main"),
                                                  ("/parent-targets.html", "child")):
                        probe = open_page(session, page_path)
                        arg = {}
                        if frame_kind == "child":
                            arg = {"frame": frames(probe)[1]["frame"]}
                        observed = snap(probe, **arg)
                        for html, expected in LINKS:
                            dom_id = html.split('id="', 1)[1].split('"', 1)[0]
                            node = find(observed, "link", dom_id=dom_id)
                            want = expected
                            if want is None:
                                want = "target_cross_frame" if frame_kind == "child" else "allowed"
                            expect(tag + f"a {dom_id} link in the {frame_kind} frame is {want}",
                                   node is not None and node.get("activation") == want,
                                   {"activation": node.get("activation") if node else None})
                            if node is None or want == "allowed":
                                continue
                            state = ok("target.inspect", {"target": probe})
                            answer = call("target.act", {"target": probe, "reference": node["reference"],
                                                         "action": {"kind": "click"}})
                            still = ok("target.inspect", {"target": probe})
                            expect(tag + f"clicking it is refused {want} and moves nothing",
                                   refused(answer, "unsupported_capability", want)
                                   and still["url"] == state["url"]
                                   and still["revision"] == state["revision"],
                                   {"error": answer.get("error")})
                        ok("target.close", {"target": probe})

                    # 11b. A base target decides an activation this host does not model.
                    based = open_page(session, "/base-target.html")
                    observed = snap(based)
                    without = find(observed, "link", dom_id="nobase")
                    own = find(observed, "link", dom_id="own")
                    expect(tag + "a base target refuses an activation that names no target of its own",
                           without is not None and without.get("activation") == "base_target_unmodeled"
                           and own is not None and own.get("activation") == "allowed",
                           {"without": (without or {}).get("activation"),
                            "own": (own or {}).get("activation")})
                    answer = call("target.act", {"target": based, "reference": without["reference"],
                                                 "action": {"kind": "click"}}) if without else {}
                    expect(tag + "and the click is refused with that reason rather than treated as self",
                           refused(answer, "unsupported_capability", "base_target_unmodeled"),
                           {"error": answer.get("error")})
                    ok("target.close", {"target": based})

                    # 11c. Form method and target, with the submitter's overrides.
                    forms = open_page(session, "/forms.html")
                    observed = snap(forms)
                    cases = {"plain": "allowed", "fmpost": "form_method_unsupported",
                             "ftblank": "target_named", "ftself": "allowed",
                             "faction": "allowed", "pplain": "form_method_unsupported",
                             "fmget": "allowed", "bplain": "target_named"}
                    for dom_id, want in cases.items():
                        node = find(observed, "button", dom_id=dom_id)
                        expect(tag + f"the {dom_id} submitter is {want}",
                               node is not None and node.get("activation") == want,
                               {"activation": (node or {}).get("activation")})
                    node = find(observed, "button", dom_id="fmpost")
                    state = ok("target.inspect", {"target": forms})
                    answer = call("target.act", {"target": forms, "reference": node["reference"],
                                                 "action": {"kind": "press", "key": "enter"}}) if node else {}
                    still = ok("target.inspect", {"target": forms})
                    expect(tag + "a formmethod that is not GET is refused before any event",
                           node is not None
                           and refused(answer, "unsupported_capability", "form_method_unsupported")
                           and still["revision"] == state["revision"]
                           and still["url"] == state["url"],
                           {"error": answer.get("error"), "url_tail": (still.get("url") or "").rsplit("/", 1)[-1]})
                    ok("target.close", {"target": forms})
                    forms = open_page(session, "/forms.html")
                    observed = snap(forms)
                    node = find(observed, "button", dom_id="faction")
                    answer = call("target.act", {"target": forms, "reference": node["reference"],
                                                 "action": {"kind": "press", "key": "enter"}}) if node else {}
                    landed = ok("target.inspect", {"target": forms})
                    expect(tag + "a formaction is honoured rather than ignored",
                           node is not None and answer.get("ok")
                           and "/child-landed.html" in (landed.get("url") or ""),
                           {"url_tail": (landed.get("url") or "").rsplit("/", 1)[-1]})
                    ok("target.close", {"target": forms})

                    # 12. The main frame's handler revision behaviour is preserved.
                    handler = open_page(session, "/handler.html")
                    observed = snap(handler)
                    form = find(observed, "form")
                    before_h = revision(handler)
                    failed = call("target.act", {"target": handler, "reference": form["reference"],
                                                 "action": {"kind": "submit"}})
                    after_h = revision(handler)
                    marks = [n.get("name") for n in nodes_of(snap(handler)) if n.get("role") == "text"]
                    # The invariant is (C): a failed action advances only for
                    # what its handlers really changed, which is more than
                    # nothing and is not the action's own one.
                    expect(tag + "a failed submit keeps its handler's effects and advances only by them",
                           not failed.get("ok")
                           and after_h > before_h
                           and any("handler one" in (m or "") for m in marks)
                           and any("handler two" in (m or "") for m in marks),
                           {"revision": [before_h, after_h],
                            "advanced_by": after_h - before_h})
                    ok("target.close", {"target": handler})

                    # 2b. A script-free main document advances by exactly one.
                    quiet = open_page(session, "/quiet.html")
                    observed = snap(quiet)
                    box = find(observed, "textbox")
                    r4 = revision(quiet)
                    call("target.act", {"target": quiet, "reference": box["reference"],
                                        "action": {"kind": "set_value", "value": "court-fake-five"}})
                    expect(tag + "a script-free main frame advances by exactly one, like a child",
                           revision(quiet) == r4 + 1, {"revision": revision(quiet)})
                    ok("target.close", {"target": quiet})
                finally:
                    host.finish()
    finally:
        server.shutdown()

    leaked = [word for word in ("court-fake", "child alpha", "parent alpha")
              if any(word in response for response in responses if '"audit"' in response)]
    receipt = {
        "court": "native-dom frame-aware actions (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks),
        "limitations": [
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
            "children run no scripts, so an action in a child can never be canceled by the page",
            "the memory criteria live in the child-frame court and are rerun there unchanged",
            "no pid, path, window or desktop fact is recorded",
        ],
        "page_text_in_audit_responses": leaked,
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"]}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:200])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
