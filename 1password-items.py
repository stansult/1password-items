#!/usr/bin/env python3
"""Search 1Password items and preview bulk edits using the official op CLI."""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime


PAGE_SIZE = 10


class RunOutput:
    def __init__(self, now):
        self.now = now
        self.first_date = None
        self.date_stream = None

    def message(self, message, *, file=None):
        stream = sys.stdout if file is None else file
        current = self.now()
        if self.first_date is None:
            self.first_date = current.date()
            self.date_stream = stream
            print(f"[{self.first_date.isoformat()}]", file=stream, flush=True)
        print(f"[{current:%H:%M:%S}] {message}", file=stream, flush=True)

    def cli_message(self, message, details):
        self.message(message, file=sys.stderr)
        if details and details.strip():
            print(details.rstrip(), file=sys.stderr, flush=True)

    def finish(self):
        if self.first_date is not None:
            ending_date = self.now().date()
            if ending_date != self.first_date:
                print(f"[{ending_date.isoformat()}]", file=self.date_stream, flush=True)


@dataclass
class Edit:
    arguments: list[str]
    preview: str


def op_json(*arguments, output=None):
    result = subprocess.run(
        ["op", *arguments, "--format", "json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    if output is not None and result.stderr.strip():
        output.cli_message("1Password CLI diagnostic.", result.stderr)
    return json.loads(result.stdout)


def search_items(output, args):
    filters = []
    if args.category:
        filters.extend(["--categories", args.category])
    if args.vault:
        filters.extend(["--vault", args.vault])

    matches = [
        item for item in op_json("item", "list", *filters, output=output)
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


class QuitDialog(Exception):
    """The user quit the command or closed its input."""


def ask(output, question, options, *, pending_edit=False, parse=None):
    keys = [key for label, _ in options for key in label.split(" / ")]
    if "q" not in keys:
        quit_label = "Quit without applying changes" if pending_edit else "Quit"
        options = [*options, ("q", quit_label)]
        keys.append("q")
    width = max(len(key) for key, _ in options)
    output.message(question)
    print()
    for key, label in options:
        print(f"  {key:<{width}}    {label}")
    print()

    while True:
        try:
            answer = input("Selection: " if parse else "Choice: ").strip().casefold()
        except EOFError:
            raise QuitDialog from None
        finally:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                print()
        if answer == "q":
            raise QuitDialog
        if not answer:
            continue
        if parse:
            try:
                return parse(answer)
            except ValueError as error:
                output.message(error)
        else:
            answer = {"yes": "y", "no": "n"}.get(answer, answer)
            if answer in keys:
                return answer
            output.message(f"Enter {', '.join(keys[:-1])}, or {keys[-1]}.")


def show_pages(output, rows, *, kind, stop_option, pending_edit=False):
    shown = 0
    while shown < len(rows):
        page = rows[shown:shown + PAGE_SIZE]
        print()
        for row in page:
            print(row)
        print()
        shown += len(page)
        if shown == len(rows):
            break

        remaining = len(rows) - shown
        if remaining > PAGE_SIZE:
            question = f"Show the next {PAGE_SIZE} of {remaining} remaining {kind}s?"
        elif remaining == 1:
            question = f"Show the next {kind}?"
        else:
            question = f"Show the next {remaining} {kind}s?"
        if ask(output, question, [("y", "Show the next page"), stop_option],
               pending_edit=pending_edit) == "n":
            break
    return shown


def show_errors(output, errors):
    try:
        show_pages(output, errors, kind="error", stop_option=("n", "Finish viewing errors"))
    except QuitDialog:
        pass


def edit_error(error):
    if isinstance(error, subprocess.CalledProcessError):
        detail = " ".join((error.stderr or "").split())
        message = f"1Password CLI exited with status {error.returncode}"
        return f"{message}: {detail}" if detail else message
    return str(error)


def parse_selection(answer, count, shown):
    if answer == "all":
        return list(range(count))
    if answer == "shown":
        return sorted(shown)

    selected = set()
    for part in answer.split(","):
        match = re.fullmatch(r"([0-9]+)(?:\s*-\s*([0-9]+))?", part.strip())
        if not match:
            raise ValueError("Enter all, shown, item numbers or ranges, or q.")
        try:
            first = int(match[1])
            last = int(match[2]) if match[2] is not None else first
        except ValueError:
            raise ValueError(f"Use item numbers from 1 to {count}.") from None
        if not 1 <= first <= last <= count:
            raise ValueError(f"Use item numbers from 1 to {count} and ranges in ascending order.")
        selected.update(range(first - 1, last))
    return sorted(selected)


def confirm_selection(output, rows, selected, shown):
    while True:
        unseen = [index for index in selected if index not in shown]
        count = len(selected)
        question = f"Apply changes to {count} {'item' if count == 1 else 'items'}?"
        options = [("y", "Apply changes"), ("n", "Go back to item selection")]
        if unseen:
            remaining = len(unseen)
            unseen_message = (
                "1 item hasn't been previewed." if remaining == 1
                else f"{remaining} items haven't been previewed."
            )
            question = f"{question} {unseen_message}"
            options.append(("p", f"Preview the {remaining} unseen {'item' if remaining == 1 else 'items'}"))

        answer = ask(output, question, options, pending_edit=True)
        if answer != "p":
            return answer == "y"
        displayed = show_pages(
            output, [rows[index] for index in unseen], kind="unseen selected item",
            stop_option=("n", "Return to confirmation"), pending_edit=True,
        )
        shown.update(unseen[:displayed])


def select_items(output, rows):
    count = len(rows)
    shown = set(range(show_pages(
        output, rows, kind="proposed change", stop_option=("n", "Continue to item selection"), pending_edit=True,
    )))
    while True:
        selected = ask(
            output, f"You previewed {len(shown)} of {count} proposed changes.",
            [
                ("all", f"Select all {count} items" if count > 1 else "Select the only item"),
                ("shown", f"Select the {len(shown)} previewed items" if len(shown) > 1
                 else "Select the previewed item"),
                ("3-5,8", "Example: select items 3, 4, 5, and 8"),
            ],
            pending_edit=True,
            parse=lambda answer: parse_selection(answer, count, shown),
        )
        if confirm_selection(output, rows, selected, shown):
            return selected


def report_edit_progress(processed, total, *, inline):
    prefix = "\r" if inline and processed else ""
    end = "" if inline and processed < total else "\n"
    print(f"{prefix}Applying changes… {processed}/{total} items processed.", end=end, flush=True)


def apply_edits(output, args, items):
    planned = []
    errors = []
    for item in items:
        try:
            edit = prepare_edit(args, item)
        except (ValueError, subprocess.CalledProcessError) as error:
            errors.append(f"Skipped {describe(item)} — {edit_error(error)}")
            continue
        if edit is not None:
            planned.append((item, edit))

    if not planned:
        output.message("No changes to apply.")
        if errors:
            output.message(f"Skipped due to errors: {len(errors)}.")
            show_errors(output, errors)
        return int(bool(errors))

    output.message(f"{len(planned)} proposed changes.")
    try:
        selected = select_items(output, [
            f"{number}. {describe(item)} — {edit.preview}"
            for number, (item, edit) in enumerate(planned, start=1)
        ])
    except QuitDialog:
        output.message("Canceled.")
        return 0

    applied = 0
    up_to_date = 0
    total = len(selected)
    inline = sys.stdout.isatty()
    for processed, index in enumerate(selected):
        if inline or processed == 0:
            report_edit_progress(processed, total, inline=inline)
        item, edit = planned[index]
        try:
            current = get_item(item)
            if current["title"] != item["title"] or current.get("archived", False):
                raise ValueError("item changed since the preview; run the command again")
            refreshed = prepare_edit(args, current, current)
            if refreshed is None:
                up_to_date += 1
                continue
            if args.command == "change" and refreshed != edit:
                raise ValueError("field changed since the preview; run the command again")
            subprocess.run(
                ["op", "item", "edit", item["id"], "--vault", item["vault"]["id"],
                 *refreshed.arguments],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
        except (ValueError, subprocess.CalledProcessError) as error:
            errors.append(f"{index + 1}. Skipped {describe(item)} — {edit_error(error)}")
            continue
        applied += 1

    if inline:
        report_edit_progress(total, total, inline=True)
    changes = "change" if applied == 1 else "changes"
    output.message(f"Applied {applied} {changes}. Skipped due to errors: {len(errors)}.")
    if up_to_date:
        output.message(f"Already up to date: {up_to_date}.")
    show_errors(output, errors)
    return int(bool(errors))


def parse_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("--title", metavar="REGEX", help="case-insensitive title pattern")
    filters.add_argument("--has-tag", help="match an exact existing tag")
    filters.add_argument("--category", help="1Password category, such as Login")
    filters.add_argument("--vault", help="vault name or ID")

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("search", parents=[filters], help="count and optionally list matching items")
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


def main(*, now=datetime.now):
    args = parse_arguments()
    output = RunOutput(now)
    try:
        items = search_items(output, args)
        if not items:
            output.message("No matching items.")
            return 0
        if args.command == "search":
            count = len(items)
            summary = f"{count} matching {'item' if count == 1 else 'items'}."
            if count == 1:
                question = "Show it?"
            elif count <= PAGE_SIZE:
                question = f"Show all {count} matching items?"
            else:
                question = f"Show the first {PAGE_SIZE}?"
            if ask(output, f"{summary} {question}", [
                ("y", "Show results"), ("n / q", "Exit search"),
            ]) == "y":
                show_pages(output, [describe(item) for item in items],
                           kind="result", stop_option=("n / q", "Exit search"))
            return 0
        return apply_edits(output, args, items)
    except FileNotFoundError:
        output.message("The 1Password CLI (op) is missing from PATH.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        output.cli_message(f"1Password CLI failed (exit {error.returncode}).", error.stderr)
        return 1
    except QuitDialog:
        return 0
    finally:
        output.finish()


if __name__ == "__main__":
    sys.exit(main())
