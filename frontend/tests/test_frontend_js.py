"""Every inline <script> on every page must be valid JavaScript.

A page can render perfectly and still be broken: Jinja happily renders a
truncated expression like ``{{ name|tojson }`` into JavaScript that no browser
will parse, and the whole script block silently dies.  A page-level HTTP test
cannot see that, so the scripts are parsed here.

The test is skipped when Node.js is not installed.
"""

import os
import re
import shutil
import subprocess
import tempfile

import pytest

SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)

PAGES = [
    "/",
    "/login",
    "/register",
    "/mentor/login",
    "/dashboard.html",
    "/overview.html",
    "/profile.html",
    "/resume.html",
    "/roadmap.html",
    "/skillgap.html",
    "/projects.html",
    "/quiz.html",
    "/progress.html",
    "/mentors.html",
    "/mentors/become.html",
    "/mentorship.html",
    "/messages.html",
    "/network.html",
]

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def _inline_scripts(html):
    return [match.group(1) for match in SCRIPT_RE.finditer(html)]


def _check_with_node(source, label):
    """Return the list of syntax errors reported by node, if any."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        path = handle.name
    try:
        result = subprocess.run(
            [NODE, "--check", path], capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return []
        return [line for line in result.stderr.splitlines() if line.strip()]
    finally:
        os.unlink(path)


@requires_node
def test_the_public_pages_have_valid_inline_javascript(client):
    for path in ["/", "/login", "/register", "/mentor/login"]:
        html = client.get(path).get_data(as_text=True)
        for index, script in enumerate(_inline_scripts(html)):
            errors = _check_with_node(script, f"{path} script {index}")
            assert not errors, f"{path} inline script {index} is invalid:\n" + "\n".join(errors)


@requires_node
def test_the_app_pages_have_valid_inline_javascript(client, user_factory):
    """Every page behind the login, including the mentor detail page."""
    test_client, _ = user_factory("js@example.test", "JS Tester")

    # A mentor profile so /mentors/<id> renders the request modal.
    test_client.post(
        "/api/mentors/me",
        json={"headline": "JS mentor", "expertise": "python, sql"},
    )
    from models import User

    with client.application.app_context():
        user = User.query.filter_by(email="js@example.test").first()
        mentor_user_id = user.id

    for path in PAGES:
        if path in ("/", "/login", "/register", "/mentor/login"):
            continue
        html = test_client.get(path).get_data(as_text=True)
        for index, script in enumerate(_inline_scripts(html)):
            errors = _check_with_node(script, f"{path} script {index}")
            assert not errors, f"{path} inline script {index} is invalid:\n" + "\n".join(errors)

    # The mentor detail page is the one that carries the request modal.
    html = test_client.get(f"/mentors/{mentor_user_id}").get_data(as_text=True)
    for index, script in enumerate(_inline_scripts(html)):
        errors = _check_with_node(script, f"mentor detail script {index}")
        assert not errors, "the mentor detail page has invalid JavaScript:\n" + "\n".join(errors)


@requires_node
def test_the_request_modal_script_is_valid(client, user_factory, app):
    """A regression test for the exact bug that broke the request button."""
    from models import User

    student_client, student = user_factory("js2@example.test", "JS Student")
    mentor_client, mentor = user_factory("js3@example.test", "JS Mentor")
    mentor_client.post(
        "/api/mentors/me",
        json={"headline": "JS mentor", "expertise": "python, sql"},
    )
    with app.app_context():
        user = User.query.filter_by(email="js3@example.test").first()
        mentor_id = user.id

    html = student_client.get(f"/mentors/{mentor_id}").get_data(as_text=True)
    scripts = _inline_scripts(html)
    assert scripts, "the mentor detail page has no inline script"

    for script in scripts:
        errors = _check_with_node(script, "mentor detail")
        assert not errors, "invalid JavaScript on the mentor detail page:\n" + "\n".join(errors)

    # The name must be a proper JSON string in the rendered script…
    assert '"JS Mentor"' in html
    # …and it must not be glued to the next literal (the bug that broke it:
    # `{{ name|tojson }` rendered as `"JS Mentor".", "success")`).
    assert '"JS Mentor".' not in html
