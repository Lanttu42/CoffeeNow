#!/bin/sh
# Hakee lastdrop.carrata.fi:n varmenteen Leafyn certbotilla ja ottaa HTTPS-konffin käyttöön.
# Aja, kun DNS osoittaa tähän koneeseen:  sh ~/servers/coffeenow/deploy/lastdrop-tls.sh
# Leafyn certbot uusii varmenteen jatkossa automaattisesti.
set -e
ME=178.105.22.24
D=lastdrop.carrata.fi
CONF=~/servers/leafy/web/conf.d/lastdrop.conf
HERE=$(cd "$(dirname "$0")" && pwd)
[ "$(dig +short A $D @1.1.1.1 | tail -1)" = "$ME" ] || { echo "$D ei vielä osoita $ME:hen (julkinen DNS) - odota."; exit 1; }
[ -f "$CONF" ] || { cp "$HERE/nginx-lastdrop-http.conf" "$CONF"; docker exec leafy-web nginx -s reload; }
docker exec leafy-certbot certbot certonly --webroot -w /var/www/certbot -d $D \
    --cert-name $D --non-interactive --keep-until-expiring
cp "$CONF" /tmp/lastdrop.conf.bak
cp "$HERE/nginx-lastdrop.conf" "$CONF"
if docker exec leafy-web nginx -t; then
  docker exec leafy-web nginx -s reload && echo "OK: https://$D"
else
  echo "nginx -t epäonnistui - palautetaan edellinen konffi"; cp /tmp/lastdrop.conf.bak "$CONF"; exit 1
fi
