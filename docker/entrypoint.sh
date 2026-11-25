#!/bin/sh
# Docker creates the ./data bind mount as root; hand it to the app user, then drop root.
set -e
mkdir -p /srv/data
chown -R app:app /srv/data
exec setpriv --reuid=app --regid=app --init-groups "$@"
