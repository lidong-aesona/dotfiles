# Share portable preferences and SessionStart hooks; preserve machine-local policy.
. as $local
| $shared[0] as $shared
| ($shared | {
    statusLine,
    enabledPlugins,
    voice,
    voiceEnabled,
    skipDangerousModePermissionPrompt,
    theme,
    agentPushNotifEnabled,
    autoCompactWindow,
    modelSettings
  } | with_entries(select(.value != null))) as $portable
| $local + $portable
| if (($shared.hooks.SessionStart // []) | length) == 0 then .
  else .hooks.SessionStart = (
    reduce ($shared.hooks.SessionStart // [])[] as $hook
      (.hooks.SessionStart // [];
       if index($hook) == null then . + [$hook] else . end)
  )
  end
