#!/usr/bin/env python3
"""The frozen court for attribute-name validation.

Frozen from `attribute-name-validation-audit-0.0.2.md` §8 before the base
changes, and failing until the guard exists.

What it holds the host to: one guard in the `setAttribute` funnel, so all six
authoring surfaces validate together; the parser's own names surviving, being
read, and surviving a deep clone, because a parser and a copy are not
authoring; `removeAttribute` and `toggleAttribute(false)` staying lenient;
validation running before the name is lowercased; the `dataset` key rule the
base cannot see; `classList` keeping the vocabulary it was ruled to keep; a
thrown message carrying neither the offending name nor the value; and the
redaction still answering with its one fixed word when such a throw escapes.

The floors and the main-only slack are not measured here — the child-frame and
shim-footprint courts measure them on the same binary, and a failure there
stops the slice.

Strictly headless: no surface binary, no window, no AppKit, and it refuses to
run with the visible-court variable set. One hermetic loopback origin, both
allocators.

Groups: funnel, parser, clone, lenient, order, dataset, classList, message,
redaction.
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
import check_contract  # noqa: E402,F401

VISIBLE_ENV = "MINICON_SURF_ALLOW_VISIBLE_COURT"
BAD_NAME = "InvalidCharacterError|5|[object DOMException]"
DATASET_NAME = "SyntaxError|undefined|[object Error]"
# The ten the parser produces from the fixture below, in the order the host
# reports them. `UPPER` arrives lowercased: that is the parser's own doing.
PARSED = "-lead,.dot,1bad,aé,id,ok-name,under_score,upper,weird:name,x.y"
# An opaque value the page tries to write with a bad name. Neither it nor the
# name may come back inside the error.
WRITTEN_VALUE = "jrq8-45nzpd-1174"
BAD_WRITTEN_NAME = "9vkt-73"


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
JOBS = load_module("job_deadline_court", Path(__file__).with_name("job-deadline-court.py"))


def threw(expression):
    return ("(function(){var e=document.createElement('p');try{" + expression
            + ";return 'accepted';}catch(err){"
            "return err.name+'|'+String(err.code)+'|'"
            "+Object.prototype.toString.call(err);}})()")


PROBES = [
    # A1-A6: the six authoring surfaces that share the funnel.
    ("funnel_setattribute", threw("e.setAttribute('1bad','x')")),
    ("funnel_toggle_true", threw("e.toggleAttribute('1bad')")),
    ("funnel_dataset", threw("e.dataset[' x']='1'")),
    # id, className and classList always name a valid attribute, so what the
    # funnel must not do is break them.
    ("funnel_id",
     "(function(){var e=document.createElement('p');e.id='fine';"
     "return e.getAttribute('id')+'|'+e.getAttributeNames().join(',');})()"),
    ("funnel_classname",
     "(function(){var e=document.createElement('p');e.className='a b';"
     "return e.getAttribute('class');})()"),
    ("funnel_classlist",
     "(function(){var e=document.createElement('p');e.classList.add('one');"
     "e.classList.add('two');return e.getAttribute('class');})()"),
    # A7: every shape the guard must refuse.
    ("refuse_space", threw("e.setAttribute('a b','x')")),
    ("refuse_empty", threw("e.setAttribute('','x')")),
    ("refuse_quote", threw("e.setAttribute('a\"b','x')")),
    ("refuse_leading_dash", threw("e.setAttribute('-x','1')")),
    # A8: and the shapes it must not.
    ("accept_colon",
     "(function(){var e=document.createElement('p');e.setAttribute('ns:x','1');"
     "return e.getAttributeNames().join(',');})()"),
    ("accept_dotted",
     "(function(){var e=document.createElement('p');e.setAttribute('x.y','1');"
     "return e.getAttributeNames().join(',');})()"),
    # A9: the order — a valid name is validated as given and stored lowercased,
    # and an invalid one is refused whatever its case.
    ("order_lowercased",
     "(function(){var e=document.createElement('p');e.setAttribute('MiXed','1');"
     "return e.getAttributeNames().join(',');})()"),
    ("order_invalid_upper", threw("e.setAttribute('1BAD','x')")),
    # A10: lenient where the standard is lenient.
    ("lenient_remove",
     "(function(){var e=document.createElement('p');try{e.removeAttribute('1bad');"
     "e.removeAttribute('a b');return 'lenient';}catch(err){return 'threw:'+err.name;}})()"),
    ("lenient_toggle_false",
     "(function(){var e=document.createElement('p');try{e.toggleAttribute('1bad',false);"
     "return 'lenient';}catch(err){return 'threw:'+err.name;}})()"),
    # A11: the parser's names, read and deep-cloned.
    ("parsed_names", "document.getElementById('odd').getAttributeNames().join(',')"),
    ("parsed_clone",
     "document.getElementById('odd').cloneNode(true).getAttributeNames().join(',')"),
    ("parsed_read", "String(document.getElementById('odd').getAttribute('1bad'))"),
    # A12: the dataset key rule, and the keys it must still allow.
    ("dataset_dash", threw("e.dataset['a-b']='1'")),
    ("dataset_camel",
     "(function(){var e=document.createElement('p');e.dataset.fooBar='1';"
     "return e.getAttributeNames().join(',');})()"),
    ("dataset_digit",
     "(function(){var e=document.createElement('p');e.dataset['1x']='1';"
     "return e.getAttributeNames().join(',');})()"),
    # A13: classList keeps its own vocabulary.
    ("classlist_space", threw("e.classList.add('a b')")),
    ("classlist_empty", threw("e.classList.add('')")),
    # A14: the message carries neither the name nor the value.
    ("message_clean",
     "(function(){var e=document.createElement('p');try{"
     "e.setAttribute('" + BAD_WRITTEN_NAME + "','" + WRITTEN_VALUE + "');return 'accepted';}"
     "catch(err){return String(err.message.indexOf('" + BAD_WRITTEN_NAME + "'))+'|'"
     "+String(err.message.indexOf('" + WRITTEN_VALUE + "'));}})()"),
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
    odd = ('1bad="a" weird:name="b" UPPER="c" -lead="d" .dot="e" ok-name="f" '
           'under_score="g" x.y="h" aé="i"')
    slots = "".join("<p id=r%d></p>" % i for i in range(len(PROBES)))
    script = "".join(
        "try{var v%d=String(%s);}catch(e){var v%d='probe-threw:'+e.name;}"
        "document.getElementById('r%d').textContent='%s='+v%d;"
        % (i, expression, i, i, name, i)
        for i, (name, expression) in enumerate(PROBES))
    PAGE = ("<!doctype html><html><body><main><p id=\"odd\" " + odd + ">odd</p>"
            + slots + "</main><script>" + script + "</script></body></html>").encode("utf-8")
    # An uncaught refusal, carrying a value the page holds.
    CRASH = ("<!doctype html><html><body><main><input id=h value='" + WRITTEN_VALUE + "'>"
             "</main><script>document.createElement('p').setAttribute("
             "'" + BAD_WRITTEN_NAME + "', document.getElementById('h').value);"
             "</script></body></html>").encode()

    class Handler(network.Handler):
        def do_GET(self):
            path, _, _query = self.path.partition("?")
            network.Handler.hits.append(path)
            if path == "/crash.html":
                return self.reply(200, CRASH)
            return self.reply(200, PAGE)

    server = network.Server(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    killed_hosts = []

    def expect(name, condition, detail=None):
        checks.append({"check": name, "passed": bool(condition),
                       **({"detail": detail} if detail is not None else {})})

    try:
        for allocator in ("system", "arena"):
            tag = f"[{allocator}] "
            with tempfile.TemporaryDirectory(prefix="minicon-surf-attrname-") as directory:
                host = JOBS.Supervised(args.binary, directory, origin, allocator)
                try:
                    profile = host.ok("profile.create", {"persistence": "ephemeral"})["profile"]
                    session = host.ok("session.open", {"profile": profile})["session"]
                    target = host.ok("target.open",
                                     {"session": session,
                                      "url": origin + "/page.html"})["target"]
                    snapshot = host.ok("target.snapshot",
                                       {"target": target, "format": "semantic",
                                        "max_bytes": 131072, "max_nodes": 128})
                    said = {}
                    for node in snapshot["nodes"]:
                        text = node.get("name") or ""
                        if node.get("role") == "text" and "=" in text:
                            key, _, value = text.partition("=")
                            said[key] = value

                    # A1-A3: the three funnel entries that can carry a name.
                    for name in ("funnel_setattribute", "funnel_toggle_true",
                                 "funnel_dataset"):
                        expect(tag + f"A1: {name} refuses a bad name through the funnel",
                               said.get(name) == BAD_NAME, {"said": said.get(name)})

                    # A4-A6: the three that always name a valid attribute keep working.
                    expect(tag + "A2: id, className and classList still write",
                           said.get("funnel_id") == "fine|id"
                           and said.get("funnel_classname") == "a b"
                           and said.get("funnel_classlist") == "one two",
                           {"id": said.get("funnel_id"),
                            "className": said.get("funnel_classname"),
                            "classList": said.get("funnel_classlist")})

                    # A7: everything the guard must refuse.
                    for name in ("refuse_space", "refuse_empty", "refuse_quote",
                                 "refuse_leading_dash"):
                        expect(tag + f"A3: {name} is refused",
                               said.get(name) == BAD_NAME, {"said": said.get(name)})

                    # A8: and what it must not.
                    expect(tag + "A4: a colon and a dot are legal names and stay legal",
                           said.get("accept_colon") == "ns:x"
                           and said.get("accept_dotted") == "x.y",
                           {"colon": said.get("accept_colon"),
                            "dotted": said.get("accept_dotted")})

                    # A9: the order.
                    expect(tag + "A5: a valid name is stored lowercased, an invalid one refused in any case",
                           said.get("order_lowercased") == "mixed"
                           and said.get("order_invalid_upper") == BAD_NAME,
                           {"lowercased": said.get("order_lowercased"),
                            "invalid_upper": said.get("order_invalid_upper")})

                    # A10: lenient where the standard is.
                    expect(tag + "A6: removeAttribute and toggleAttribute(false) stay lenient",
                           said.get("lenient_remove") == "lenient"
                           and said.get("lenient_toggle_false") == "lenient",
                           {"remove": said.get("lenient_remove"),
                            "toggle": said.get("lenient_toggle_false")})

                    # A11: the parser and the copy are not authoring.
                    expect(tag + "A7: the parser's ten names survive, read back, and deep-clone",
                           said.get("parsed_names") == PARSED
                           and said.get("parsed_clone") == PARSED
                           and said.get("parsed_read") == "a",
                           {"names": said.get("parsed_names"),
                            "clone": said.get("parsed_clone"),
                            "read": said.get("parsed_read")})

                    # A12: the dataset rule and the keys it must still allow.
                    expect(tag + "A8: a dataset key with a dash before a lowercase letter is refused",
                           said.get("dataset_dash") == DATASET_NAME,
                           {"said": said.get("dataset_dash")})
                    expect(tag + "A9: and camelCase and leading digits still write",
                           said.get("dataset_camel") == "data-foo-bar"
                           and said.get("dataset_digit") == "data-1x",
                           {"camel": said.get("dataset_camel"),
                            "digit": said.get("dataset_digit")})

                    # A13: classList unchanged.
                    expect(tag + "A10: classList keeps the vocabulary it was ruled to keep",
                           said.get("classlist_space")
                           == "InvalidCharacterError|undefined|[object Error]"
                           and said.get("classlist_empty")
                           == "SyntaxError|undefined|[object Error]",
                           {"space": said.get("classlist_space"),
                            "empty": said.get("classlist_empty")})

                    # A14: the message says the fault, not the page's strings.
                    expect(tag + "A11: the message carries neither the name nor the value",
                           said.get("message_clean") == "-1|-1",
                           {"said": said.get("message_clean")})

                    # A15: and an uncaught refusal still tells the host nothing.
                    crashed = host.call("target.open",
                                        {"session": session, "url": origin + "/crash.html"})
                    body = json.dumps(crashed, sort_keys=True)
                    error = crashed.get("error") or {}
                    details = error.get("details") or {}
                    expect(tag + "A12: an uncaught refusal still says the host's fixed word only",
                           WRITTEN_VALUE not in body and BAD_WRITTEN_NAME not in body
                           and details.get("engine_error") == "a script threw",
                           {"value_present": WRITTEN_VALUE in body,
                            "name_present": BAD_WRITTEN_NAME in body,
                            "engine_error": details.get("engine_error")})
                finally:
                    if host.killed:
                        killed_hosts.append({"allocator": allocator})
                    host.finish()
                    killed_hosts.extend({"allocator": allocator, **t} for t in host.timeouts)
    finally:
        server.shutdown()

    receipt = {
        "court": "native-dom attribute-name validation (control 0.0.2)",
        "host_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
        "expected": {"bad_name": BAD_NAME, "dataset_key": DATASET_NAME,
                     "parser_names": PARSED.count(",") + 1},
        "checks": checks,
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
        "passed": all(c["passed"] for c in checks) and not killed_hosts,
        "hosts_killed": killed_hosts,
        "limitations": [
            "design-frozen court: it fails until the setAttribute funnel validates",
            "the M1 and M2 floors and the main-only slack are measured by the child-frame and shim-footprint courts on the same binary; a failure there stops the slice",
            "the guard approximates the XML Name production in ASCII by ruling, so a non-ASCII name the parser produces cannot be authored; this court pins the parser half and does not test authoring one",
            "one hermetic loopback origin, macOS only; no surface, no window, no AppKit",
        ],
    }
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"passed": receipt["passed"], "checks_passed": receipt["checks_passed"],
                      "checks_total": receipt["checks_total"],
                      "hosts_killed": len(killed_hosts)}))
    for check in checks:
        if not check["passed"]:
            print("FAIL", json.dumps(check)[:170])
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
