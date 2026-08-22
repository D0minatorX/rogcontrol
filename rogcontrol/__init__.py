"""ROG Control.

Only the version lives here. Anything else in a package __init__ is imported
by every module that touches the package -- including the helper scripts and
the tests, which must never pull in GTK -- so this file stays empty of logic
on purpose.
"""

# Shown on the System page's About row. Matches the version the GTK3 app
# reports, because the two are the same product from the user's side: this
# is a new window on the same config, helper and profiles, not a new app.
APP_VERSION = "1.0.0.3"
