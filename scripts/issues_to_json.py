import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


TITLE_TYPE_RE = re.compile(r"^\[(RSS|APP)\]\s+", re.IGNORECASE)


def load_event_payload(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_title_type(title):
    if not title:
        return None
    match = TITLE_TYPE_RE.match(title.strip())
    if not match:
        return None
    return match.group(1).lower()


def parse_issue_form_body(body):
    if not body:
        return {}

    data = {}
    current_key = None
    lines = body.splitlines()
    heading_re = re.compile(r"^###\s+(.+?)\s*$")

    key_alias = {
        "data type": "type",
        "type": "type",
        "blog name": "name",
        "app name": "name",
        "name": "name",
        "description": "description",
        "tags": "tags",
        "rss url": "rss_url",
        "git repo url": "git_repo_url",
        "url": "url",
    }

    for line in lines:
        m = heading_re.match(line.strip())
        if m:
            raw = m.group(1).strip().lower()
            current_key = key_alias.get(raw)
            if current_key and current_key not in data:
                data[current_key] = ""
            continue

        if not current_key:
            continue

        value = line.strip()
        if value in ("_No response_", ""):
            continue
        if data[current_key]:
            data[current_key] += "\n" + value
        else:
            data[current_key] = value

    return data


def parse_tags(value):
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        parts = re.split(r"[,，\n]", value)
        items = [p.strip() for p in parts if p.strip()]
    else:
        items = []

    normalized = []
    for item in items:
        clean = re.sub(r"^[-*]\s*", "", str(item).strip())
        if clean:
            normalized.append(clean)
    return normalized


def valid_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def canonicalize_url(url):
    parsed = urllib.parse.urlparse(url.strip())
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if not path:
        path = "/"
    return urllib.parse.urlunparse(
        (parsed.scheme.lower(), netloc, path, parsed.params, parsed.query, "")
    )


def validate_payload(payload, expected_type):
    errors = []

    payload_type = str(payload.get("type", "")).strip().lower()
    if payload_type == "":
        errors.append("缺少字段: type")
    elif payload_type != expected_type:
        errors.append(
            "type 与标题前缀不一致: 标题为 [{}]，提交为 {}".format(
                expected_type.upper(), payload_type
            )
        )

    name = str(payload.get("name", "")).strip()
    if not name:
        errors.append("缺少字段: name")

    description = str(payload.get("description", "")).strip()
    if not description:
        errors.append("缺少字段: description")

    tags = parse_tags(payload.get("tags"))
    if len(tags) == 0:
        errors.append("缺少字段: tags")
    if len(tags) > 4:
        errors.append("tags 数量不能超过 4")

    if expected_type == "rss":
        url = str(payload.get("rss_url") or payload.get("url") or "").strip()
        if not url:
            errors.append("缺少字段: rss_url")
        elif not valid_url(url):
            errors.append("rss_url 不是有效的 http/https URL")
    else:
        url = str(payload.get("git_repo_url") or "").strip()
        if not url:
            errors.append("缺少字段: git_repo_url")
        elif not valid_url(url):
            errors.append("git_repo_url 不是有效的 http/https URL")

        if len(tags) > 0 and tags[0].lower() not in ("plugin", "theme"):
            errors.append("APP 的首个 tag 必须是 plugin 或 theme")

    normalized = {
        "type": expected_type,
        "name": name,
        "description": description,
        "tags": tags,
        "rss_url": str(payload.get("rss_url") or payload.get("url") or "").strip(),
        "git_repo_url": str(payload.get("git_repo_url") or "").strip(),
        "raw_type": payload_type,
    }

    return errors, normalized


def build_validation_message(errors):
    lines = [
        "格式校验未通过：",
        "",
    ]
    for item in errors:
        lines.append("- " + item)

    lines.extend(
        [
            "",
            "请修改 issue 内容后保存。",
        ]
    )
    return "\n".join(lines)


def post_issue_comment(repo, issue_number, token, message):
    if not repo or not issue_number or not token:
        return False

    url = "https://api.github.com/repos/{}/issues/{}/comments".format(repo, issue_number)
    body = json.dumps({"body": message}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", "Bearer {}".format(token))
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as _:
            return True
    except urllib.error.HTTPError as e:
        print("Failed to post comment: HTTP {}".format(e.code))
    except urllib.error.URLError as e:
        print("Failed to post comment: {}".format(e.reason))
    return False


def load_json_array(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def upsert_record(records, new_record):
    key = new_record["dedupe_key"]
    for idx, record in enumerate(records):
        if record.get("dedupe_key") == key:
            records[idx] = new_record
            return records
    records.append(new_record)
    return records


def detect_review_action(labels):
    names = {str(item.get("name", "")).strip().lower() for item in labels}
    if "del" in names:
        return "del"
    if "pick" in names:
        return "pick"
    if "def" in names:
        return "def"
    return None


def remove_record_by_issue_number(records, issue_number):
    filtered = [
        item for item in records if item.get("issue", {}).get("number") != issue_number
    ]
    return filtered, len(records) - len(filtered)


def main():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")

    if not event_path or not os.path.exists(event_path):
        print("Missing GITHUB_EVENT_PATH")
        return 1

    event = load_event_payload(event_path)

    if event_name != "issues":
        print("Skip: only issues events are supported")
        return 0

    issue = event.get("issue", event)
    if not isinstance(issue, dict):
        print("No issue payload found")
        return 1

    action = str(event.get("action", "")).strip().lower()
    issue_number = issue.get("number")
    issue_title = issue.get("title", "")
    expected_type = parse_title_type(issue_title)
    issue_body = issue.get("body", "")
    payload = parse_issue_form_body(issue_body)

    if action in ("opened", "edited"):
        validation_errors = []

        if expected_type is None:
            validation_errors.append("标题必须以 [RSS] 或 [APP] 开头")

        if not payload:
            validation_errors.append("未解析到 issue 模板字段，请使用指定模板提交")

        if not validation_errors:
            errors, _ = validate_payload(payload, expected_type)
            validation_errors.extend(errors)

        if validation_errors:
            post_issue_comment(repo, issue_number, token, build_validation_message(validation_errors))
            print("Validation failed on {}".format(action))
            for err in validation_errors:
                print("- " + err)
            return 0

        print("Validation passed on {}".format(action))
        return 0

    if action not in ("labeled", "unlabeled"):
        print("Skip: unsupported issues action {}".format(action))
        return 0

    labels = issue.get("labels", [])
    review_action = detect_review_action(labels)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(root, "data", "issues")
    all_path = os.path.join(output_dir, "all.json")
    rss_path = os.path.join(output_dir, "rss.json")
    app_path = os.path.join(output_dir, "app.json")

    all_items = load_json_array(all_path)

    if review_action == "del":
        all_items, removed = remove_record_by_issue_number(all_items, issue_number)
        rss_items = [item for item in all_items if item.get("type") == "rss"]
        app_items = [item for item in all_items if item.get("type") == "app"]
        write_json(all_path, all_items)
        write_json(rss_path, rss_items)
        write_json(app_path, app_items)
        print("Removed {} record(s) for issue #{}".format(removed, issue_number))
        return 0

    if review_action not in ("pick", "def"):
        print("Skip: issue does not contain pick/def/del label")
        return 0

    if expected_type is None:
        print("Invalid title prefix, skip")
        return 0

    if not payload:
        print("No issue form data found, skip")
        return 0

    errors, normalized = validate_payload(payload, expected_type)
    if errors:
        print("Validation failed")
        for err in errors:
            print("- " + err)
        return 0

    now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    record_url = (
        normalized["rss_url"] if expected_type == "rss" else normalized["git_repo_url"]
    )
    canonical_url = canonicalize_url(record_url)
    dedupe_key = "{}|{}".format(expected_type, canonical_url)

    record = {
        "dedupe_key": dedupe_key,
        "type": expected_type,
        "review": review_action,
        "name": normalized["name"],
        "description": normalized["description"],
        "tags": normalized["tags"],
        "rss_url": normalized["rss_url"],
        "git_repo_url": normalized["git_repo_url"],
        "issue": {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "url": issue.get("html_url"),
            "updated_at": issue.get("updated_at"),
        },
        "source": {
            "event": event_name,
            "from": "issue",
        },
        "canonical_url": canonical_url,
        "collected_at": now,
    }

    all_items = upsert_record(all_items, record)
    all_items.sort(key=lambda x: (x.get("type", ""), x.get("name", "").lower()))

    rss_items = [item for item in all_items if item.get("type") == "rss"]
    app_items = [item for item in all_items if item.get("type") == "app"]

    write_json(all_path, all_items)
    write_json(rss_path, rss_items)
    write_json(app_path, app_items)

    print("Updated JSON files: all={}, rss={}, app={}".format(len(all_items), len(rss_items), len(app_items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
