"""Decky backend bridge to the installed ROG Control command line."""

import asyncio
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess

import decky


class Plugin:
    def _command(self, args, timeout, privileged=False):
        user_home = Path(getattr(decky, "DECKY_USER_HOME", Path.home()))
        launcher = user_home / ".local" / "bin" / "rogcontrol"
        if not launcher.is_file():
            found = shutil.which("rogcontrol")
            if found:
                launcher = Path(found)
            else:
                raise RuntimeError(
                    "ROG Control was not found. Install it for the Decky user first.")

        argv = [str(launcher), "decky", *args]
        env = dict(os.environ)
        env["HOME"] = str(user_home)
        if os.geteuid() == 0:
            account = pwd.getpwuid(user_home.stat().st_uid)
            env["USER"] = account.pw_name
            env["LOGNAME"] = account.pw_name
            runtime = Path(f"/run/user/{account.pw_uid}")
            if runtime.is_dir():
                env["XDG_RUNTIME_DIR"] = str(runtime)
                if (runtime / "bus").exists():
                    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
            else:
                env.pop("XDG_RUNTIME_DIR", None)
                env.pop("DBUS_SESSION_BUS_ADDRESS", None)
            env["PATH"] = f"{user_home}/.local/bin:" + env.get("PATH", "/usr/bin:/bin")
            if not privileged:
                runuser = shutil.which("runuser")
                if not runuser:
                    raise RuntimeError("Cannot switch to the Decky user to access ROG Control")
                argv = [runuser, "--user", account.pw_name, "--", *argv]

        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=env,
            check=False)
        try:
            result = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as error:
            detail = completed.stderr.strip() or "ROG Control returned invalid JSON"
            raise RuntimeError(detail) from error
        if completed.returncode != 0 or not result.get("ok"):
            raise RuntimeError(result.get("error") or "ROG Control request failed")
        return result

    async def _call(self, args, timeout=30, privileged=False):
        try:
            return await asyncio.to_thread(self._command, args, timeout, privileged)
        except Exception as error:
            return {"ok": False, "error": str(error)}

    async def get_state(self):
        result = await self._call(["state"])
        return result.get("state") if result.get("ok") else result

    async def get_telemetry(self):
        result = await self._call(["telemetry"], timeout=15)
        return result.get("telemetry") if result.get("ok") else result

    async def set_gpu_clock_limit(self, mhz: int):
        return await self._call(["gpu", str(mhz)])

    async def set_profile(self, name: str):
        result = await self._call(["profile", name])
        if not result.get("ok"):
            return result
        request_id = result.get("request_id")
        if not request_id:
            return {"ok": False, "error": "Update ROG Control to use profile completion feedback."}
        for _attempt in range(120):
            await asyncio.sleep(0.5)
            status = await self._call(["profile-status", request_id])
            if not status.get("ok"):
                return status
            if status.get("status") == "applied":
                return {"ok": True, "profile": name}
            if status.get("status") in ("failed", "superseded"):
                return {"ok": False, "error": status.get("error") or "Profile request was superseded."}
        return {"ok": False, "error": "Profile application has not finished; check ROG Control."}

    async def set_cpu_boost(self, enabled: bool):
        return await self._call(["cpu", "boost", "on" if enabled else "off"])

    async def set_cpu_max_freq(self, mhz: int):
        return await self._call(["cpu", "max-freq", str(mhz)])

    async def check_for_update(self):
        return await self._call(["update-check"])

    async def install_update(self):
        result = await self._call(["update-install"], timeout=180, privileged=(os.geteuid() == 0))
        if result.get("ok"):
            result["message"] = "Update installed. Reload Decky to activate it."
        return result

    async def _main(self):
        decky.logger.info("ROG Control plugin loaded")

    async def _unload(self):
        decky.logger.info("ROG Control plugin unloaded")
