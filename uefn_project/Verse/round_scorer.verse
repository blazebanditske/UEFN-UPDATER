using { /Fortnite.com/Devices }
using { /Fortnite.com/Characters }
using { /Verse.org/Simulation }
using { /UnrealEngine.com/Temporary/Diagnostics }

# Template for a device-driven mechanic.
#
# This is the shape an agent should follow when adding gameplay: a
# creative_device subclass, @editable fields so a creator can tune it in the
# Details panel without touching code, and subscriptions to other devices'
# events so it composes with the rest of the island.
#
# Compile it after writing (`uefn-link verse build`); Verse is statically
# checked and the build result is the only reliable correctness signal.
round_scorer := class(creative_device):

    # Tunable in the Details panel. @editable fields are the contract between
    # this mechanic and the creator.
    @editable
    PointsPerRound : int = 10

    @editable
    AnnounceToPlayers : logic = true

    # Assign a device placed in the level; wiring devices rather than looking
    # them up by name keeps the mechanic usable across islands.
    @editable
    RoundManager : round_settings_device = round_settings_device{}

    var Score : int = 0

    # Called once when the game starts. Suspends is required to await events.
    OnBegin<override>()<suspends>:void=
        Print("uefn-link: round_scorer ready")
        # Awaiting the round start keeps the handler alive for the match.
        # Any event exposed by an @editable device can be awaited here.
        RoundManager.RoundBeginEvent.Subscribe(OnRoundBegin)

    OnRoundBegin(MaybePlayer : ?player):void=
        set Score += PointsPerRound
        if (AnnounceToPlayers?):
            Print("uefn-link: round scored, total {Score}")
