# mbzuai-aldarmaki-web-app

## Command for docker deployment:
`docker compose down; docker compose up --build`

## Fix for flask server permissions issues:
If the flask_server container will not startup, it's likely because of permissions issues with the flask/logs and flask/_uploads folders.
Solution: `sudo chown 999:999 flask/logs; sudo chown 999:999 flask/logs/_placeholder; sudo chown 999:999 flask/_uploads; sudo chown 999:999 flask/_uploads/_placeholder`
