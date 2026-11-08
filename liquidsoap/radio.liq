# Hausfunk playout: one request queue and one Icecast mount per channel.
#
# The orchestrator pushes items over the telnet interface:  <slug>.push <uri>
# While a channel's queue is empty it plays the files in TEST_MEDIA_DIR (or silence).
#
# NOTE: written for Liquidsoap 2.4.x but not yet run. Check with
#   podman compose run --rm liquidsoap --check /radio/radio.liq

settings.server.telnet := true
settings.server.telnet.bind_addr := "0.0.0.0"
settings.server.telnet.port := 1234

icecast_host = environment.get("ICECAST_HOST", default="icecast")
icecast_port = int_of_string(environment.get("ICECAST_PORT", default="8000"))
icecast_password = environment.get("ICECAST_SOURCE_PASSWORD", default="")
test_dir = environment.get("TEST_MEDIA_DIR", default="/media/test")
channels = string.split(
  separator=",", environment.get("LIQUIDSOAP_CHANNELS", default="hausfunk")
)

if icecast_password == "" then
  log.critical("ICECAST_SOURCE_PASSWORD is not set")
  shutdown()
end

def channel(slug) =
  queue = request.queue(id=slug)
  test = playlist(id="#{slug}_test", mode="randomize", test_dir)
  radio = fallback(id="#{slug}_main", track_sensitive=false, [queue, test])
  radio = mksafe(crossfade(radio))
  ignore(
    output.icecast(
      %mp3(bitrate=192),
      id="#{slug}_out",
      host=icecast_host,
      port=icecast_port,
      password=icecast_password,
      mount="/#{slug}",
      name=slug,
      radio
    )
  )
end

list.iter(channel, channels)
