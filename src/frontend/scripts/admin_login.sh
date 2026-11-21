# admin_login.sh — administrator login for the repository's shell scripts.
# Source it; do not run it.
#
# The server has no built-in password any more. The credentials are picked with
# the precedence src/start_server.sh starts the server with:
#   1. the DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD environment pair;
#   2. the launcher's local-mode file src/server/auth-local/admin_credentials
#      (username on line 1, password on line 2), created by the first plain
#      ./src/start_server.sh;
#   3. the pair the calling script passes as its fallback.
# The Python scripts use the same precedence through admin_login.py.
#
#   . "$REPO/src/frontend/scripts/admin_login.sh"
#   dcdtuni_admin_credentials admin '<fallback password>'  # sets DCDTUNI_LOGIN_*
#   dcdtuni_curl_login "$BASE" "$COOKIE"                    # curl login, cookie jar
#   browser-use eval "$(dcdtuni_browser_login_js)"          # prints "result: 200"

DCDTUNI_CREDENTIALS_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/src/server/auth-local/admin_credentials"

# JSON string literal for $1 (quotes, backslashes and the usual control characters).
_dcdtuni_json_string() {
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}; s=${s//$'\r'/\\r}; s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

# Sets DCDTUNI_LOGIN_USER, DCDTUNI_LOGIN_PASSWORD, DCDTUNI_LOGIN_SOURCE and
# DCDTUNI_LOGIN_JSON (the POST /api/auth/login body). $1/$2: fallback pair.
dcdtuni_admin_credentials() {
  local fallback_user=${1:-} fallback_password=${2:-} user="" password=""
  if [[ -n "${DCDTUNI_ADMIN_USER:-}" && -n "${DCDTUNI_ADMIN_PASSWORD:-}" ]]; then
    user=$DCDTUNI_ADMIN_USER; password=$DCDTUNI_ADMIN_PASSWORD
    DCDTUNI_LOGIN_SOURCE="DCDTUNI_ADMIN_USER/DCDTUNI_ADMIN_PASSWORD"
  elif [[ -r "$DCDTUNI_CREDENTIALS_FILE" ]] && {
         IFS= read -r user; IFS= read -r password || [[ -n "$password" ]]
       } < "$DCDTUNI_CREDENTIALS_FILE" && [[ -n "$user" && -n "$password" ]]; then
    user=${user#"${user%%[![:space:]]*}"}; user=${user%"${user##*[![:space:]]}"}
    password=${password%$'\r'}
    DCDTUNI_LOGIN_SOURCE=$DCDTUNI_CREDENTIALS_FILE
  elif [[ -n "$fallback_user" && -n "$fallback_password" ]]; then
    user=$fallback_user; password=$fallback_password
    DCDTUNI_LOGIN_SOURCE="the script's own pair"
  else
    echo "no administrator credentials: set DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD," \
         "or start the server once with ./src/start_server.sh to create $DCDTUNI_CREDENTIALS_FILE" >&2
    return 1
  fi
  DCDTUNI_LOGIN_USER=$user
  DCDTUNI_LOGIN_PASSWORD=$password
  DCDTUNI_LOGIN_JSON="{\"username\":$(_dcdtuni_json_string "$user"),\"password\":$(_dcdtuni_json_string "$password")}"
}

# curl login: $1 base URL, $2 cookie jar to write. Fails unless the server says ok.
dcdtuni_curl_login() {
  local base=$1 jar=$2 reply
  [[ -n "${DCDTUNI_LOGIN_JSON:-}" ]] || dcdtuni_admin_credentials || return 1
  reply=$(curl -s -c "$jar" -X POST "$base/api/auth/login" \
            -H "Content-Type: application/json" --data-binary "$DCDTUNI_LOGIN_JSON") || reply=""
  if [[ "$reply" != *'"ok": true'* && "$reply" != *'"ok":true'* ]]; then
    echo "login to $base refused (credentials from $DCDTUNI_LOGIN_SOURCE): ${reply:-no reply}" >&2
    return 1
  fi
}

# JavaScript for `browser-use eval`: a synchronous same-origin login that sets
# the page's session cookie and evaluates to the HTTP status (200 = logged in).
dcdtuni_browser_login_js() {
  [[ -n "${DCDTUNI_LOGIN_JSON:-}" ]] || dcdtuni_admin_credentials || return 1
  printf '%s' "(function(){var x=new XMLHttpRequest();x.open('POST','/api/auth/login',false);x.setRequestHeader('Content-Type','application/json');x.send(JSON.stringify($DCDTUNI_LOGIN_JSON));return x.status;})()"
}
