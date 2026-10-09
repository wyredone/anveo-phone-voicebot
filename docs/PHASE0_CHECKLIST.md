# Real Windows/Anveo acceptance test

## Before calling

- [ ] Use the exact Python executable that runs your working AnveoPhone.
- [ ] Close the normal AnveoPhone instance; disable its Auto-answer setting.
- [ ] Run `doctor`; confirm native import, AudioMediaPort and Qt checks pass.
- [ ] Launch the PCM server and host; confirm SIP registration succeeds.
- [ ] Use a test phone and tell the tester that this test captures audio.

## Armed call

- [ ] Incoming call rings normally and remains unanswered until manually answered.
- [ ] After answering, Events shows "Live PCM connected".
- [ ] Caller hears the one-second tone, or the supplied test WAV.
- [ ] Caller speaks a distinctive phrase; keep the call open at least five seconds.
- [ ] PC microphone speech does not reach caller while bridge owns call.
- [ ] Call audio does not play from PC speakers while bridge owns call.
- [ ] Captured WAV contains caller's phrase, without the returned response mixed in.
- [ ] JSON report has nonzero input, response frames and zero callback errors in host stats.
- [ ] Hangup ends capture, releases ports and stops the network worker.

## Recovery and defaults

- [ ] F9 during a call restores the earlier PC mute/monitor settings.
- [ ] A subsequent incoming call without F8 stays a normal manual call.
- [ ] F8 and a new answered call use a new session/capture file.
- [ ] Stop the PCM server during a test; host restores manual audio and logs failure.
- [ ] Put test call on hold, then resume; no duplicate routing or stale response audio.
- [ ] Quit host; no native crash, stuck call or busy RTP port.
- [ ] Relaunch normal AnveoPhone and confirm normal calls still work.

Phase 0 is accepted only after audible response and identifiable caller capture
are verified on this Windows PC and its actual Anveo SIP path. Passing automated
tests alone does not complete these checks.
