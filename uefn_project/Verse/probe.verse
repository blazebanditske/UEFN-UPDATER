# Minimal Verse file used to verify the link end to end.
#
# Writing this and compiling it exercises the full path: file write, project
# sandbox, Verse compiler, and result reporting. If this builds clean, the
# link works.
using { /Fortnite.com/Devices }
using { /Verse.org/Simulation }

uefn_link_probe := class(creative_device):

    OnBegin<override>()<suspends>:void=
        Print("uefn-link probe: link is live")
