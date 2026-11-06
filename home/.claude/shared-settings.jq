# Share portable preferences and every shared hook; preserve machine-local policy.
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
| reduce (($shared.hooks // {}) | to_entries[]) as $event (.;
    .hooks[$event.key] = (
      reduce $event.value[] as $hook
        (.hooks[$event.key] // [];
         if index($hook) == null then . + [$hook] else . end)
    ))
