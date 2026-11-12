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

Both edit commands require a search filter. At the numbered preview, enter item numbers separated by commas, enter `all`, or press Return on an empty line to cancel.

Each selected item is saved separately. Successful changes stay applied if a later edit fails. The script continues after individual errors, reports the skipped items, and exits with status 1 when the batch reports errors. Review the summary before rerunning a command. A search can still match items that were updated, depending on its filters.

If the CLI reports an unsupported field such as `ssoLogin`, edit that item in the 1Password app. Items with existing tags containing commas also need to be tagged in the app.

## Verification

Search, selection, title and named-field changes, tag preservation, and continuation after a rejected edit were checked with simulated CLI responses. The unified script's edit commands have yet to be verified against a live test vault.

Run `python3 1password-items.py --help` or append `--help` to any subcommand for more details.
