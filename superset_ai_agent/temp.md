Good. That means the old stale `//static` issue is gone.

I found the next issue in our nginx rule: `location /static` is too broad. It sends all `/static/...` traffic to `superset-node`, but Flask/AppBuilder still owns paths like `/static/appbuilder/...`. I narrowed nginx to proxy only webpack assets:

```nginx
location /static/assets {
    proxy_pass http://superset_node;
}
```

Changed:
- [docker-compose.yml](/Users/lohzh/superset/docker-compose.yml:89)
- [docker-compose.no-bind.yml](/Users/lohzh/superset/docker-compose.no-bind.yml:64)
- [superset.conf.template](/Users/lohzh/superset/docker/nginx/templates/superset.conf.template:50)

Validated compose config, rendered nginx config for `/` and `/superset`, `git diff --check`, and targeted pre-commit.

After pulling/rebuilding on Windows, verify:

```powershell
docker exec superset-nginx-1 sh -lc "grep -n 'location .*static' /etc/nginx/conf.d/superset.conf"
```

Expected:

```text
location /static/assets {
```

Then check both routes:

```powershell
Invoke-WebRequest http://localhost:8090/static/assets/manifest.json -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest http://localhost:8090/static/appbuilder/css/flags/flags16.css -UseBasicParsing | Select-Object StatusCode
```

Both should return `200`, but they should be served by different upstreams: assets by `superset-node`, appbuilder static by `superset`.