-- Exo OS desktop. This file stays small and is not generated.
-- Everything a person can change (keys, apps, text size, keyboard languages, idle times) is rendered by
-- Exo OS from the machine profile into exo/profile.lua. Ask Exo to change it, or run `exo-os set`.

local generated = os.getenv("HOME") .. "/.config/hypr/exo/profile.lua"
local f = io.open(generated, "r")
if f then
  f:close()
  dofile(generated)
end

-- Keys that always work, even if Exo is not running.
hl.bind("SUPER + W", hl.dsp.window.close(), { description = "Close window" })
hl.bind("SUPER + F", hl.dsp.window.fullscreen({ mode = "fullscreen" }), { description = "Full screen" })
for i = 1, 9 do
  hl.bind("SUPER + ALT + " .. i, hl.dsp.focus({ workspace = tostring(i) }), { description = "Workspace " .. i })
end
hl.bind("SUPER + ESCAPE", hl.dsp.exec_cmd("loginctl lock-session"), { description = "Lock" })

hl.config({
  misc = { disable_hyprland_logo = true, disable_splash_rendering = true },
  ecosystem = { no_update_news = true, no_donation_nag = true },
  general = { layout = "dwindle" },
})

-- Hand the session to systemd: the prompt bar and idle handling run as user services.
hl.on("hyprland.start", function()
  hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE && systemctl --user start exo-desktop.target")
end)
