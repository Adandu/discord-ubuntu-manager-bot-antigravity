# DiscoBunty v0.9.5

## New Features

- No new end-user features were added in this release.

## Improvements

- Tightened the dashboard form-control CSS so the app now overrides Tailwind's generated forms reset on the concrete input, textarea, and select elements used by the WebUI.

## Bug Fixes

- Fixed the remaining white-background form regression caused by Tailwind's `[type='text']` and related base selectors overriding the dashboard's dark field styling.
- Restored readable dark-mode rendering for the Core Configuration inputs and the add-server modal fields, including Port and password-auth fields.
