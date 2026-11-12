# 1Password Items

Search saved 1Password items by title, tag, category, or vault. Preview bulk field changes and tag additions, then choose which items to update.

## Setup

- Python 3.9 or newer
- [1Password CLI 2](https://www.1password.dev/cli/get-started), available as `op` on your PATH
- An authenticated 1Password CLI session with access to the vaults you want to use

On macOS, you can enable CLI authentication in the 1Password desktop app under **Settings → Developer → Integrate with 1Password CLI**.

## Search

```sh
python3 1password-items.py search --category Login --title 'google|gmail'
```

Search shows the number of matches first. Enter `y` to display the first page. At any search prompt, enter `n` or `q` to exit search. With no filters, the count covers all active items available to the current CLI account.

All three commands accept the same filters. Combine them to narrow the results.

| Filter | Matches |
| --- | --- |
| `--title REGEX` | A case-insensitive regular expression applied to the title |
| `--has-tag TAG` | An exact existing tag |
| `--category CATEGORY` | A 1Password category, such as `Login` |
| `--vault VAULT` | A vault name or ID |

## Change a field

Replace a title prefix on selected items.

```sh
python3 1password-items.py change --category Login --has-tag Workday \
  --replace 'Myworkdayjobs - ' 'Workday | ' --prefix
```

The default field is `title`. Use `--field` for an existing plain-text field and `--section` to distinguish fields with the same name. Use `--set VALUE` to set the full value or `--replace OLD NEW` for a literal, case-sensitive replacement. Add `--prefix` to limit the replacement to the beginning of the value.

The preview displays both the current and proposed values. The script passes the proposed value as an argument to `op`, so it may be visible to other local processes. Use this command for non-sensitive values.

## Add a tag

```sh
python3 1password-items.py tag 'Google Accounts' --category Login --title 'google|gmail'
```

The tag can be new or already in use. The script keeps existing tags and skips items that already have the requested tag.

## Review and apply changes

Search results, edit previews, and error lists display at most 10 items per page. Enter `y` at a paging prompt to continue, or `n` to stop listing and continue to the step named in the prompt.

Both edit commands require a search filter. Item numbers stay fixed across the whole batch. At the selection prompt, enter one of the following:

- `all` selects the entire batch, including items you haven't previewed.
- `shown` selects every item displayed so far.
- Numbers and ranges such as `1,4,7-11, 20-22` select those items from the batch. Overlapping entries count once.
- `q` quits without applying changes.

Confirm the selection with `y` to apply it, or enter `n` to go back to item selection. If any selected items are still unseen, enter `p` to preview just those items with their original numbers, then confirm again. Items displayed using `p` also count toward `shown`.

Every prompt lists its options vertically. Enter `q` at any prompt to quit immediately. If edits have already been applied, they stay saved. A blank response repeats the prompt without a warning. Invalid input shows a hint and repeats the same prompt.

After confirmation, the script shows how many items it has processed. The counter updates in place in an interactive terminal. Each selected item is saved separately. Successful changes stay applied if a later edit fails. The final summary counts successful edits, skipped errors, and items already up to date. Only errors are listed, with their original preview numbers when available. The script continues after individual errors and exits with status 1 when the batch reports errors. Review the summary before rerunning a command. A search can still match items that were updated, depending on its filters.

If the CLI reports an unsupported field such as `ssoLogin`, edit that item in the 1Password app. Items with existing tags containing commas also need to be tagged in the app.

## Timestamps

Operational output starts with the local date in `[YYYY-MM-DD]`. Primary messages such as counts, questions, input hints, and summaries start with local `[HH:MM:SS]`. Menu choices, input prompts, item rows, error details, and the live progress counter stay undecorated. Help and argument usage errors are also undecorated.

An ending date appears only when the local date at exit differs from the date of the first operational message.

## Verification

Search, selection, title and named-field changes, tag preservation, and continuation after a rejected edit were checked with simulated CLI responses. The unified script's edit commands have yet to be verified against a live test vault.

Run `python3 1password-items.py --help` or append `--help` to any subcommand for more details.

## Run the tests

```sh
python3 -B -m unittest discover -s tests -v
```

The suite runs the script against a shared fake `op` command and needs no 1Password account or credentials. GitHub Actions runs it on pushes and pull requests with Python 3.9 and 3.15. [Live test vault verification](https://github.com/stansult/1password-items/issues/3) is manual.
