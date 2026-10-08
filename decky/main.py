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
    def _command(self, args, timeout):
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
            env["PATH"] = f"{user_home}/.local/bin:" + env.get("PATH", "/usr/bin:/bin")
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

    async def _call(self, args, timeout=30):
        try:
            return await asyncio.to_thread(self._command, args, timeout)
        except Exception as error:
            return {"ok": False, "error": str(error)}

    async def get_state(self):
        result = await self._call(["state"])
        return result.get("state") if result.get("ok") else result

    async def set_profile(self, name: str):
        result = await self._call(["profile", name])
        if not result.get("ok"):
            return result
        for _attempt in range(40):
            await asyncio.sleep(0.25)
            state = await self.get_state()
            if isinstance(state, dict) and state.get("current_profile") == name:
                return {"ok": True, "profile": name, "state": state}
        return {"ok": False,
                "error": "Profile request was accepted but did not become active."}

    async def set_cpu_boost(self, enabled: bool):
        return await self._call(["cpu", "boost", "on" if enabled else "off"])

    async def set_cpu_max_freq(self, mhz: int):
        return await self._call(["cpu", "max-freq", str(mhz)])

    async def check_for_update(self):
        return await self._call(["update-check"])

    async def install_update(self):
        result = await self._call(["update-install"], timeout=180)
        if result.get("ok"):
            result["message"] = "Update installed. Reload Decky to activate it."
        return result

    async def _main(self):
        decky.logger.info("ROG Control plugin loaded")

    async def _unload(self):
        decky.logger.info("ROG Control plugin unloaded")
