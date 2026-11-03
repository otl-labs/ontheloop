"""The producer-side transport plane of the telemetry loop (T2.36, slice 2a-2, plan 4; agreed with the slice 3b thread).

Stdlib only, plus src.telemetry.blobs, src.telemetry.request and src.telemetry.timestamps. It may touch the network, but it
reaches the write boundary only through a transport callable its caller injects (Decision 184 cl.2, Decision 212 cl.2); it
never imports the write boundary, src.common, scripts or src.turn_capture. Slice 3b adds its own modules here.
"""
