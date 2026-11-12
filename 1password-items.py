#!/usr/bin/env python3
"""Search 1Password items and preview bulk edits using the official op CLI."""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Edit:
    arguments: list[str]
    preview: str


def op_json(*arguments):
    result = subprocess.run(
        ["op", *arguments, "--format", "json"],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def search_items(args):
    filters = []
    if args.category:
        filters.extend(["--categories", args.category])
    if args.vault:
        filters.extend(["--vault", args.vault])

    matches = [
        item for item in op_json("item", "list", *filters)
        if (args.title is None or args.title.search(item["title"]))
        and (args.has_tag is None or args.has_tag in (item.get("tags") or []))
        and not item.get("archived", False)
    ]
    return sorted(matches, key=lambda item: item["title"].casefold())


def describe(item):
    return f"{item['title']!r} [{item['vault']['name']}] ({item['category']})"


def get_item(item):
    return op_json("item", "get", item["id"], "--vault", item["vault"]["id"])


def field_assignment(details, name, section):
    sections = {entry["id"]: entry for entry in details.get("sections", [])}
    matches = []
    for field in details.get("fields", []):
        reference = field.get("section") or {}
        owner = sections.get(reference.get("id"), reference)
        if name not in (field.get("id"), field.get("label")):
            continue
        if section is not None and section not in (owner.get("id"), owner.get("label")):
            continue
        matches.append((field, owner))

    if len(matches) != 1:
        raise ValueError("field is missing or ambiguous; check its name and --section")
    field, owner = matches[0]
    if field["type"] not in {"STRING", "EMAIL", "PHONE", "URL"}:
        raise ValueError("field is not a supported plain-text field")

    parts = [owner["id"], field["id"]] if owner else [field["id"]]
    if not all(re.fullmatch(r"[a-zA-Z0-9_]+", part) for part in parts):
        raise ValueError("field or section ID cannot be used in a CLI assignment")
    return ".".join(parts), field.get("value") or ""


def prepare_edit(args, item, details=None):
    if args.command == "tag":
        tags = (details if details is not None else item).get("tags") or []
        if args.tag_name in tags:
            return None
        if any("," in tag for tag in tags):
            raise ValueError("an existing tag contains a comma; use the Mac app")
        return Edit(["--tags", ",".join([*tags, args.tag_name])],
                    f"add tag {args.tag_name!r}")

    if args.field == "title":
        assignment = None
        old_value = item["title"]
    else:
        if details is None:
            details = get_item(item)
        assignment, old_value = field_assignment(details, args.field, args.section)

    if args.replace is None:
        new_value = args.set_value
    else:
        old_text, replacement = args.replace
        if args.prefix:
            if not old_value.startswith(old_text):
                return None
            new_value = replacement + old_value[len(old_text):]
        else:
            new_value = old_value.replace(old_text, replacement)

    if new_value == old_value:
        return None
    if assignment is None:
        if not new_value.strip():
            raise ValueError("the new title is blank")
        arguments = ["--title", new_value]
    else:
        arguments = [f"{assignment}={new_value}"]
    return Edit(arguments, f"{old_value!r} → {new_value!r}")


def select_items(count):
    answer = input("Apply to which items? Enter numbers, all, or Return to cancel: ").strip()
    if not answer:
        return []
    if answer.casefold() == "all":
        return list(range(count))
    try:
        numbers = sorted({int(part) for part in answer.split(",")})
    except ValueError:
        raise SystemExit("Enter comma-separated item numbers.")
    if numbers[0] < 1 or numbers[-1] > count:
        raise SystemExit("An item number is outside the preview.")
    return [number - 1 for number in numbers]


def apply_edits(args, items):
    planned = []
    failures = 0
    for item in items:
        try:
            edit = prepare_edit(args, item)
        except (ValueError, subprocess.CalledProcessError) as error:
            print(f"Skipped {describe(item)} — {error}", file=sys.stderr)
            failures += 1
            continue
        if edit is not None:
            planned.append((item, edit))

    if not planned:
        print("No changes to apply.")
        return int(bool(failures))

    for number, (item, edit) in enumerate(planned, start=1):
        print(f"{number}. {describe(item)} — {edit.preview}")

    selected = select_items(len(planned))
    if not selected:
        print("Canceled.")
        return 0

    applied = 0
    for index in selected:
        item, edit = planned[index]
        try:
            current = get_item(item)
            if current["title"] != item["title"] or current.get("archived", False):
                raise ValueError("item changed since the preview; run the command again")
            refreshed = prepare_edit(args, current, current)
            if refreshed is None:
                print(f"Already up to date: {describe(item)}")
                continue
            if args.command == "change" and refreshed != edit:
                raise ValueError("field changed since the preview; run the command again")
            result = subprocess.run(
                ["op", "item", "edit", item["id"], "--vault", item["vault"]["id"],
                 *refreshed.arguments],
                stdout=subprocess.DEVNULL,
            )
            if result.returncode:
                raise ValueError(f"1Password rejected the edit (exit {result.returncode})")
        except (ValueError, subprocess.CalledProcessError) as error:
            print(f"Skipped {describe(item)} — {error}", file=sys.stderr)
            failures += 1
            continue
        applied += 1
        print(f"Updated {describe(item)}")

    print(f"Applied {applied} changes. Skipped due to errors: {failures}.")
    return int(bool(failures))


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("--title", metavar="REGEX", help="case-insensitive title pattern")
    filters.add_argument("--has-tag", help="match an exact existing tag")
    filters.add_argument("--category", help="1Password category, such as Login")
    filters.add_argument("--vault", help="vault name or ID")

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("search", parents=[filters], help="list matching items")
    change = commands.add_parser("change", parents=[filters], help="preview and edit a field")
    change.add_argument("--field", default="title", help="title or an existing plain-text field")
    change.add_argument("--section", help="section name or ID for a named field")
    operation = change.add_mutually_exclusive_group(required=True)
    operation.add_argument("--set", dest="set_value", metavar="VALUE", help="set the entire value")
    operation.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"),
                           help="replace literal, case-sensitive text")
    change.add_argument("--prefix", action="store_true", help="replace only a leading match")
    tag = commands.add_parser("tag", parents=[filters], help="preview and add a tag")
    tag.add_argument("tag_name", help="existing or new tag name")

    args = parser.parse_args()
    if args.command != "search" and not any(
        (args.title, args.has_tag, args.category, args.vault)
    ):
        parser.error("specify at least one search filter for a bulk edit")
    if args.title is not None:
        try:
            args.title = re.compile(args.title, re.IGNORECASE)
        except re.error as error:
            parser.error(f"invalid title pattern: {error}")
    if args.command == "change":
        if args.prefix and args.replace is None:
            parser.error("--prefix requires --replace")
        if args.replace is not None and not args.replace[0]:
            parser.error("--replace requires nonempty OLD text")
        if args.field == "title" and args.section is not None:
            parser.error("--section applies only to named fields")
    if args.command == "tag":
        args.tag_name = args.tag_name.strip()
        if not args.tag_name or "," in args.tag_name:
            parser.error("enter a nonempty tag name without commas")
    return args


def main():
    args = parse_arguments()
    try:
        items = search_items(args)
        if not items:
            print("No matching items.")
            return 0
        if args.command == "search":
            for item in items:
                print(describe(item))
            print(f"{len(items)} matching items.")
            return 0
        return apply_edits(args, items)
    except FileNotFoundError:
        raise SystemExit("The 1Password CLI (op) is missing from PATH.")
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"1Password CLI failed (exit {error.returncode}).")
    except EOFError:
        raise SystemExit("No selection received. Canceled.")


if __name__ == "__main__":
    sys.exit(main())
