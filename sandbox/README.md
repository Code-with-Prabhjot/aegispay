# AegisPay sandbox runner image

Minimal image used by `DockerExecutionRunner` to run untrusted worker code
(Fix #6 in the architecture dossier). Non-root user, pytest preinstalled,
`WORKDIR /workspace`, `ENTRYPOINT ["pytest"]`.

Build:

```bash
docker build -t aegispay-sandbox-runner:latest sandbox/
```

At run time the arbiter adds `--network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m
--memory 256m --memory-swap 256m --cpus 0.5 --pids-limit 64
--security-opt no-new-privileges:true --cap-drop ALL` and mounts the job directory
read-only at `/workspace`. Select the runner with `SANDBOX_MODE=docker|subprocess|auto`.
