#!/usr/bin/env sh
set -eu

curl --fail --silent --show-error \
  -X POST \
  -H "X-Admin-Key: northstar-local-admin" \
  http://localhost:4010/admin/api/reset

printf "\nNorthstar dealership platform reset.\n"

