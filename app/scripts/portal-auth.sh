#!/bin/sh
# portal-auth.sh — 校园网 Portal 自动认证（容器版）
# 只做一件事：探测本机校园网上联口是否被放行，未放行就用 302 跳转里的参数重新认证。
# 全部环境参数动态获取：门户地址(portal base)、AC 地址(ac-ip)、本机地址(uaddress)、本机 MAC(umac)
# 都从 AC 返回的 302 Location 里解析，脚本不含任何硬编码 IP。
# 用法： portal-auth.sh [iface ...]   不给参数 = 自动处理所有有默认路由的接口
# 配置：
#   /config/creds.json    user / pass（统一账号密码，必填）
#   /config/portal.json   umac（被认证网卡 MAC，可留空）/ uplinkMac（上联设备 MAC，可留空）
#   /defaults/api.json     probe_url（探测地址，可缺省）
CONFIG_DIR=${CONFIG_DIR:-/config}
DEFAULTS_DIR=${DEFAULTS_DIR:-/defaults}
STATE_DIR=${STATE_DIR:-/state}
LOG=$STATE_DIR/logs/portal-auth.log
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# ---- 读 JSON 配置（python3 解析） ----
jget() {
    python3 -c "import json,sys;d=json.load(open(sys.argv[1],encoding='utf-8'));v=d.get(sys.argv[2],'');print('' if v is None else v)" "$1" "$2" 2>/dev/null
}

PORTAL_USER=$(jget "$CONFIG_DIR/creds.json" user)
PORTAL_PASS=$(jget "$CONFIG_DIR/creds.json" pass)
UMAC_CFG=$(jget "$CONFIG_DIR/portal.json" umac)
UPLINK_CFG=$(jget "$CONFIG_DIR/portal.json" uplinkMac)
PROBE=$(jget "$DEFAULTS_DIR/api.json" probe_url)
[ -z "$PROBE" ] && PROBE=http://connect.rom.miui.com/generate_204

: "${PORTAL_USER:?missing user in $CONFIG_DIR/creds.json}"
: "${PORTAL_PASS:?missing pass in $CONFIG_DIR/creds.json}"

# ---- 本机参数（动态） ----
if_ip()  { ip -4 addr show dev "$1" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1; }
if_mac() { ip link show dev "$1" 2>/dev/null | awk '/link\/ether/{print $2}' | tr ':' '-'; }

# ---- 从 Location 提取门户参数（动态） ----
qget()    { echo "$1" | sed -n "s/.*[?&]$2=\([^&]*\).*/\1/p" | head -1; }
base_of() { echo "$1" | sed -n 's#^\([a-z]*://[^/]*\).*#\1#p'; }

# ---- 探测该接口：输出 "<http_code> <location>" ----
probe() {
    curl -s -o /dev/null -D - --max-time 8 --interface "$1" "$PROBE" 2>/dev/null \
      | tr -d '\r' \
      | awk 'NR==1{c=$2} tolower($1)=="location:"{l=$2} END{print c" "l}'
}

# ---- 提交认证 ----
login() {
    iface=$1; loc=$2
    uaddress=$(qget "$loc" uaddress); [ -z "$uaddress" ] && uaddress=$(if_ip "$iface")
    umac=$(qget "$loc" umac);         [ -z "$umac" ]     && umac=$(if_mac "$iface")
    [ -n "$UMAC_CFG" ] && umac="$UMAC_CFG"       # 配置了被认证 MAC 则覆盖
    apmac="$UPLINK_CFG"                          # 上联设备 MAC（留空则传空）
    acip=$(qget "$loc" ac-ip)
    ssid=$(qget "$loc" ssid)
    ppid=$(qget "$loc" pushPageId)
    base=$(base_of "$loc")
    if [ -z "$base" ]; then log "$iface: no portal entry in redirect, skip"; return 1; fi
    resp=$(curl -s --max-time 15 -X POST "$base/portalauth/login" \
        -H 'X-Requested-With: XMLHttpRequest' \
        --data-urlencode "userName=$PORTAL_USER" \
        --data-urlencode "userPass=$PORTAL_PASS" \
        --data-urlencode "authType=1" \
        --data-urlencode "uaddress=$uaddress" \
        --data-urlencode "umac=$umac" \
        --data-urlencode "acip=$acip" \
        --data-urlencode "ssid=$ssid" \
        --data-urlencode "pushPageId=$ppid" \
        --data-urlencode "agreed=1" \
        --data-urlencode "esn=" --data-urlencode "apmac=$apmac" --data-urlencode "armac=" \
        --data-urlencode "accessMac=" --data-urlencode "businessType=" \
        --data-urlencode "registerCode=" --data-urlencode "questions=" \
        --data-urlencode "dynamicValidCode=" --data-urlencode "validCode=" 2>/dev/null)
    if echo "$resp" | grep -Eq '"success"[[:space:]]*:[[:space:]]*true'; then
        log "$iface: re-auth OK  ip=$uaddress mac=$umac ac=$acip portal=$base"; return 0
    fi
    log "$iface: re-auth FAIL  $(echo "$resp" | head -c 200)"; return 1
}

# ---- 主流程 ----
if [ $# -gt 0 ]; then
    ifaces="$*"
else
    ifaces=$( {
        ip route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}'
        ip -4 -o addr show 2>/dev/null | awk '$4 ~ /^10\./ && $2 !~ /^(br-|docker|veth|lo)/ {print $2}'
    } | sort -u )
fi
[ -z "$ifaces" ] && { log "no default route, skip"; exit 0; }

for iface in $ifaces; do
    [ -z "$(if_ip "$iface")" ] && continue          # 接口无 IPv4（未上联）→ 跳过
    res=$(probe "$iface"); code=${res%% *}; loc=${res#* }
    case "$code" in
        204) ;;                                     # 已放行 → 静默
        3*)  log "$iface: redirected, auth -> $loc"; login "$iface" "$loc" ;;
        "")  ;;                                     # 无响应（链路不通/探测失败）→ 静默
        *)   log "$iface: probe http $code" ;;
    esac
done
