"""Seed script — populate channels, users, memberships, shipments, and messages.

Run with: python -m app.seed
Idempotent: skips rows that already exist (checked by natural key).
Messages are skipped if the channel already has any messages.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.db import async_session_factory
from app.models import Channel, Membership, Message, Shipment, User

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CHANNELS = [
    {"name": "general", "description": "Company-wide announcements and updates"},
    {"name": "route-east", "description": "Eastern corridor route coordination"},
    {"name": "warehouse-mumbai", "description": "Mumbai warehouse operations"},
    {"name": "dispatch-ops", "description": "Dispatch team daily operations"},
    {"name": "delays", "description": "Delay alerts and escalations"},
]

USERS = [
    {"email": "dispatcher@hemut.com", "display_name": "Priya Dispatcher", "password": "password123"},
    {"email": "driver@hemut.com", "display_name": "Ravi Driver", "password": "password123"},
    {"email": "akash.yadav@hemut.com", "display_name": "Akash Yadav", "password": "password123"},
]

_now = datetime.utcnow()


def _t(hours_ago: float) -> datetime:
    return _now - timedelta(hours=hours_ago)


SHIPMENTS = [
    {"shipment_ref": "SHIP-001", "origin": "Bangalore", "destination": "Mumbai",
     "carrier": "FedEx", "status": "IN_TRANSIT", "eta": _now + timedelta(days=1)},
    {"shipment_ref": "SHIP-002", "origin": "Delhi", "destination": "Chennai",
     "carrier": "DHL", "status": "DELIVERED", "eta": _now - timedelta(days=1)},
    {"shipment_ref": "SHIP-003", "origin": "Hyderabad", "destination": "Pune",
     "carrier": "Ecom Express", "status": "IN_TRANSIT", "eta": _now + timedelta(days=3)},
    {"shipment_ref": "SHIP-004", "origin": "Kolkata", "destination": "Ahmedabad",
     "carrier": "Blue Dart", "status": "DELAYED", "eta": _now + timedelta(hours=6)},
    {"shipment_ref": "SHIP-005", "origin": "Chennai", "destination": "Delhi",
     "carrier": "DTDC", "status": "IN_TRANSIT", "eta": _now + timedelta(days=2)},
    {"shipment_ref": "SHIP-006", "origin": "Mumbai", "destination": "Kolkata",
     "carrier": "FedEx", "status": "DELIVERED", "eta": _now - timedelta(days=2)},
    {"shipment_ref": "SHIP-007", "origin": "Pune", "destination": "Hyderabad",
     "carrier": "DHL", "status": "DELAYED", "eta": _now + timedelta(hours=12)},
    {"shipment_ref": "SHIP-008", "origin": "Ahmedabad", "destination": "Bangalore",
     "carrier": "Blue Dart", "status": "IN_TRANSIT", "eta": _now + timedelta(days=4)},
    {"shipment_ref": "SHIP-009", "origin": "Jaipur", "destination": "Mumbai",
     "carrier": "Ecom Express", "status": "IN_TRANSIT", "eta": _now + timedelta(days=1, hours=6)},
    {"shipment_ref": "SHIP-010", "origin": "Nagpur", "destination": "Delhi",
     "carrier": "DTDC", "status": "DELAYED", "eta": _now + timedelta(hours=3)},
]

# ---------------------------------------------------------------------------
# Channel messages  (sender key = "dispatcher" | "driver" | "akash")
# ---------------------------------------------------------------------------

CHANNEL_MESSAGES: dict[str, list[tuple[str, str, float]]] = {
    "general": [
        ("akash",      "Good morning team! New week, fresh start. Let's keep the deliveries on track.", 47.5),
        ("dispatcher", "Morning! Quick update — SHIP-002 was delivered yesterday, client confirmed receipt. Good job Ravi.", 47.0),
        ("driver",     "Thanks Priya! Road conditions on NH48 were smooth yesterday.", 46.8),
        ("akash",      "Reminder: all delay reports must be logged in the channel before EOD. No verbal-only updates please.", 46.0),
        ("dispatcher", "Noted. Also flagging that SHIP-004 is running behind. Ravi, can you check your ETA when you get a chance?", 45.5),
        ("driver",     "On it. Hit a checkpoint outside Vadodara, might add 2 hrs to the run.", 45.2),
        ("akash",      "Copy that. I'll inform the client for SHIP-004. Please share location pin when possible.", 44.8),
        ("dispatcher", "SHIP-009 just left Jaipur warehouse. Expected to reach Mumbai in 30 hrs per current pace.", 38.0),
        ("akash",      "Good. Keep an eye on the weather forecast — there's a low-pressure system forming near Gujarat coast that might affect SHIP-009 and SHIP-001.", 37.5),
        ("driver",     "Already monitoring. Will reroute via Surat bypass if needed.", 37.0),
        ("dispatcher", "Everyone check your vehicle inspection reports are submitted for this week. Operations is asking.", 24.0),
        ("akash",      "Also — team standup moved to 9 AM tomorrow instead of 9:30. Please acknowledge.", 23.5),
        ("dispatcher", "Acknowledged ✓", 23.2),
        ("driver",     "Acknowledged ✓", 23.0),
    ],

    "route-east": [
        ("dispatcher", "SHIP-005 is on the Chennai-Delhi corridor. Ravi, you're assigned. Expected departure from Chennai depot at 6 AM.", 48.0),
        ("driver",     "Confirmed. Truck pre-checked and loaded. Departing on schedule.", 47.5),
        ("akash",      "SHIP-005 payload is high-value electronics. Please follow the sealed-truck protocol end to end.", 47.0),
        ("dispatcher", "Ravi — any updates after Nellore checkpoint?", 44.0),
        ("driver",     "Crossed Nellore at 11:30 AM. All good. ETA Hyderabad by 5 PM for rest stop.", 43.8),
        ("akash",      "Good pace. SHIP-008 also heading east — Ahmedabad to Bangalore. Different driver but let's track both on this channel.", 43.0),
        ("dispatcher", "Confirmed. SHIP-008 left Ahmedabad at 9 AM. Blue Dart carrier. Estimated 4 days.", 42.5),
        ("driver",     "Reached Hyderabad. Fuelled up and resting 2 hrs. Will push through the night to cover distance.", 38.0),
        ("akash",      "Night driving advisory: NH44 near Kurnool has roadwork between km 280–295. Factor in 45 min delay.", 37.5),
        ("driver",     "Thanks for the heads up. Will take the detour via Atmakur.", 37.2),
        ("dispatcher", "SHIP-008 update: driver reports tyre issue near Chitradurga. Arranged roadside assistance. 3-hr delay expected.", 28.0),
        ("akash",      "OK. Mark SHIP-008 as delayed in the system and notify the Bangalore warehouse. They're expecting it for an assembly line.", 27.5),
        ("dispatcher", "Done. Warehouse notified. They've pushed the assembly slot by 4 hrs.", 27.0),
        ("driver",     "Passed Nagpur! On track. SHIP-005 should reach Delhi depot tomorrow morning.", 18.0),
        ("akash",      "Excellent. Delhi receiving team has been notified. They'll have dock bay 3 ready.", 17.5),
        ("dispatcher", "Great work on SHIP-005. That's a priority client. Let's make sure handoff documentation is clean.", 17.0),
    ],

    "warehouse-mumbai": [
        ("akash",      "Mumbai warehouse — SHIP-006 arrived yesterday. Confirm unloading is complete?", 46.0),
        ("dispatcher", "Confirmed. Unloading done by 4 PM. Client picked up their consignment this morning.", 45.5),
        ("akash",      "Perfect. Clear the dock — SHIP-009 is inbound from Jaipur, arriving in ~30 hours.", 45.0),
        ("driver",     "I'm on SHIP-001 heading to Mumbai. Currently near Pune. ETA Mumbai warehouse around 6 PM today.", 36.0),
        ("dispatcher", "Copy that. Dock bay 2 will be clear. Do you have the delivery docket for SHIP-001?", 35.8),
        ("driver",     "Yes, all docs in order. Client signature form is printed.", 35.5),
        ("akash",      "SHIP-001 is for GlobalTech client — they need the serial numbers photographed on arrival. Don't skip that step.", 35.0),
        ("driver",     "Noted. Will do the photo scan before the client rep takes custody.", 34.8),
        ("dispatcher", "Also reminder — the weighbridge at the Mumbai dock entrance is under maintenance until 3 PM. Use gate 2.", 34.5),
        ("driver",     "Good to know. Thanks.", 34.2),
        ("akash",      "SHIP-009 update: left Jaipur, currently near Ajmer. On schedule. ETA Mumbai in 28 hrs.", 20.0),
        ("dispatcher", "Preparing dock bay 4 for SHIP-009. Contents are auto parts — will need the forklift team on standby.", 19.5),
        ("akash",      "Also coordinate with quality team — auto parts batch needs spot inspection on arrival per our SLA with the client.", 19.0),
        ("dispatcher", "QC team scheduled for tomorrow morning. Should line up well with SHIP-009 arrival.", 18.5),
        ("driver",     "SHIP-001 delivered! Client signed off. All good.", 12.0),
        ("akash",      "Great work Ravi. SHIP-001 closes out clean. Log it in the system.", 11.8),
    ],

    "dispatch-ops": [
        ("dispatcher", "Morning ops check. Active shipments today: SHIP-001 (Ravi, BLR→MUM), SHIP-005 (east corridor), SHIP-009 (Jaipur→MUM). All others stationary or delivered.", 47.0),
        ("akash",      "Add SHIP-003 to the watch list. Hyderabad to Pune, Ecom Express. Client has a strict delivery window.", 46.5),
        ("dispatcher", "On it. SHIP-003 is scheduled to depart Hyderabad at 2 PM today.", 46.2),
        ("driver",     "Who's driving SHIP-003? I can do it if you need backup — I'll be free after SHIP-001 drop.", 46.0),
        ("dispatcher", "We have Suresh assigned but having him confirm. Ravi, standby just in case.", 45.8),
        ("akash",      "SHIP-004 is the bigger concern right now. Blue Dart hasn't given us a revised ETA after the Vadodara checkpoint delay.", 45.0),
        ("dispatcher", "Called Blue Dart ops. They estimate 3-4 hr additional delay. New ETA is 9 PM today instead of 5 PM.", 44.5),
        ("akash",      "Client for SHIP-004 (Ahmedabad consignee) has been notified. They're OK with it but want real-time updates.", 44.0),
        ("dispatcher", "I'll ping every 2 hrs. Ravi, if you're near that area can you also check?", 43.8),
        ("driver",     "I'm on NH48, not the Vadodara stretch. But I can coordinate with the Blue Dart driver directly if you share his number.", 43.5),
        ("dispatcher", "Shared via DM. Thanks Ravi.", 43.2),
        ("akash",      "SHIP-007 is delayed too — Pune to Hyderabad. DHL hasn't responded to our escalation mail. Anyone have a contact there?", 36.0),
        ("dispatcher", "I have the regional manager's number. Calling now.", 35.8),
        ("dispatcher", "Spoke to DHL. They had a vehicle breakdown near Solapur. Replacement vehicle dispatched. ETA revised to tomorrow 8 AM.", 35.0),
        ("akash",      "Update the client. SHIP-007 consignee is a pharma company — this is time-sensitive.", 34.8),
        ("dispatcher", "Client called. They're unhappy but accepting. We're giving them a 10% credit per SLA clause.", 34.5),
        ("akash",      "Correct call. Log the SLA breach and credit in the ops sheet.", 34.0),
        ("driver",     "SHIP-001 on final approach to Mumbai. 45 mins out.", 12.5),
        ("dispatcher", "Roger. Dock team alerted.", 12.2),
    ],

    "delays": [
        ("akash",      "DELAY ALERT — SHIP-004 (Kolkata→Ahmedabad, Blue Dart): held at Vadodara checkpoint. Estimated 3-4 hr delay.", 45.0),
        ("dispatcher", "Acknowledged. Client notified. Monitoring.", 44.8),
        ("akash",      "DELAY ALERT — SHIP-007 (Pune→Hyderabad, DHL): vehicle breakdown near Solapur. Replacement dispatched. Delay ~18 hrs.", 35.5),
        ("dispatcher", "SHIP-007: SLA breach confirmed. Credit issued to client. DHL penalty clause invoked.", 34.8),
        ("akash",      "DELAY ALERT — SHIP-010 (Nagpur→Delhi, DTDC): driver reported road blockage due to an accident near Bhopal. Detour via Sagar city.", 28.0),
        ("driver",     "I have contacts at the DTDC Nagpur hub. Want me to follow up on SHIP-010?", 27.8),
        ("akash",      "Yes please. Get an ETA and share here.", 27.5),
        ("driver",     "Spoke to DTDC. Detour adds about 5 hours. New ETA: tomorrow 8 AM Delhi.", 27.0),
        ("dispatcher", "Client for SHIP-010 called in — they need it by 10 AM. We have 2-hr buffer. Should be fine.", 26.8),
        ("akash",      "Keep watching SHIP-010. If Bhopal stretch is still blocked by evening, escalate to DTDC senior ops.", 26.5),
        ("dispatcher", "SHIP-008 delay update: tyre replaced, truck back on road near Chitradurga. 3 hr delay total. ETA Bangalore revised.", 25.0),
        ("akash",      "Good. Bangalore warehouse is aware. No SLA breach on SHIP-008 — buffer was sufficient.", 24.8),
        ("driver",     "SHIP-010 cleared the Bhopal stretch. Driver messaged me — smooth sailing now.", 16.0),
        ("dispatcher", "Excellent. Removing SHIP-010 from active alert. Will archive once delivered.", 15.8),
        ("akash",      "Good resolution. Everyone — this week had 3 simultaneous delays (SHIP-004, SHIP-007, SHIP-010). Let's do a brief post-mortem on Friday to see if we can tighten vendor SLAs.", 15.0),
        ("dispatcher", "Agreed. I'll set up the calendar invite.", 14.8),
    ],
}

# ---------------------------------------------------------------------------
# DM conversations  (list of (sender_key, text, hours_ago))
# ---------------------------------------------------------------------------

DM_MESSAGES: dict[tuple[str, str], list[tuple[str, str, float]]] = {
    ("dispatcher", "driver"): [
        ("dispatcher", "Ravi, sharing the Blue Dart driver's number for SHIP-004 coordination: +91-98xxxxxxxx", 43.1),
        ("driver",     "Got it, thanks. I'll ping him now.", 43.0),
        ("driver",     "Spoke to him. He's at the checkpoint, paperwork issue. Should clear in 90 mins.", 42.5),
        ("dispatcher", "Perfect. I'll update the ops channel.", 42.3),
        ("driver",     "Also Priya — my next assignment after SHIP-001 drop, am I on SHIP-003 or free?", 12.0),
        ("dispatcher", "Suresh confirmed for SHIP-003. You're free after Mumbai drop. Take rest — long week.", 11.8),
        ("driver",     "Appreciated! Will file my expense report tonight.", 11.5),
        ("dispatcher", "Send it by Friday EOD. Finance has a cut-off.", 11.2),
        ("driver",     "Will do. One more thing — the weighbridge at Mumbai dock, any update on when it's back?", 10.5),
        ("dispatcher", "Should be fixed by tomorrow morning per the port authority update.", 10.2),
    ],

    ("akash", "dispatcher"): [
        ("akash",      "Priya, quick check — did we invoice the client for SHIP-006 delivery? Finance is asking.", 44.0),
        ("dispatcher", "Yes, invoice sent yesterday. I'll forward you the confirmation email.", 43.8),
        ("akash",      "Thanks. Also the SHIP-007 DHL situation — can you make sure the penalty is documented formally?", 35.0),
        ("dispatcher", "Already done. Logged in the vendor tracker and added to DHL's quarterly review file.", 34.8),
        ("akash",      "Good. On a different note — I think we should add a 6th channel for #route-west. We're getting busier on that corridor.", 30.0),
        ("dispatcher", "Agreed. Should I create it or do you want to?", 29.8),
        ("akash",      "Go ahead and create it. Add me and Ravi. We can onboard the west-corridor drivers next week.", 29.5),
        ("dispatcher", "Done. #route-west is live.", 29.0),
        ("akash",      "Perfect. For the Friday delay post-mortem, can you pull the SHIP-004, 007, and 010 timeline from the ops sheet?", 14.0),
        ("dispatcher", "On it. Will have it ready by Thursday evening so we can review before the call.", 13.8),
    ],

    ("akash", "driver"): [
        ("akash",      "Ravi, great job on SHIP-001 today. GlobalTech specifically mentioned that the photo documentation was thorough.", 11.0),
        ("driver",     "Thanks Akash! Glad it went smoothly. The dock team at Mumbai was also very helpful.", 10.8),
        ("akash",      "Noted — I'll pass the feedback along. Question: are you comfortable doing the SHIP-005 Delhi handoff solo or do you need support?", 10.0),
        ("driver",     "Solo is fine. I've done the Delhi depot before. Know the team there.", 9.8),
        ("akash",      "Good. Also, do you want to be considered for the west-corridor runs we're opening up? Better rest stops and shorter distances.", 9.0),
        ("driver",     "Yes definitely interested. Less overnight driving would be great.", 8.8),
        ("akash",      "I'll put your name forward. Should confirm by end of next week.", 8.5),
        ("driver",     "Appreciate it. Also flagging — the truck assigned to me (TN-01-1234) has a mild AC issue. Should get it checked before next long run.", 6.0),
        ("akash",      "Raised a maintenance ticket. Fleet team will inspect it on your next day off.", 5.8),
        ("driver",     "Perfect, thanks!", 5.5),
    ],
}


async def seed_channels(session: AsyncSession) -> list[Channel]:
    channels = []
    for ch in CHANNELS:
        result = await session.execute(
            select(Channel).where(Channel.name == ch["name"], Channel.is_dm == False)  # noqa: E712
        )
        existing = result.scalar_one_or_none()
        if existing:
            channels.append(existing)
            logger.info("Channel #%s already exists, skipping", ch["name"])
        else:
            channel = Channel(name=ch["name"], description=ch["description"], is_dm=False)
            session.add(channel)
            await session.flush()
            channels.append(channel)
            logger.info("Created channel #%s (id=%d)", ch["name"], channel.id)
    return channels


async def seed_users(session: AsyncSession) -> list[User]:
    users = []
    for u in USERS:
        result = await session.execute(select(User).where(User.email == u["email"]))
        existing = result.scalar_one_or_none()
        if existing:
            users.append(existing)
            logger.info("User %s already exists, skipping", u["email"])
        else:
            user = User(
                email=u["email"],
                display_name=u["display_name"],
                password_hash=hash_password(u["password"]),
            )
            session.add(user)
            await session.flush()
            users.append(user)
            logger.info("Created user %s (id=%d)", u["email"], user.id)
    return users


async def seed_memberships(
    session: AsyncSession, users: list[User], channels: list[Channel]
) -> None:
    for user in users:
        for channel in channels:
            result = await session.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.channel_id == channel.id,
                )
            )
            if result.scalar_one_or_none() is None:
                session.add(Membership(user_id=user.id, channel_id=channel.id))
                logger.info("Joined %s to #%s", user.email, channel.name)


async def seed_shipments(session: AsyncSession) -> None:
    for s in SHIPMENTS:
        result = await session.execute(
            select(Shipment).where(Shipment.shipment_ref == s["shipment_ref"])
        )
        if result.scalar_one_or_none() is None:
            session.add(Shipment(**s))
            logger.info("Created shipment %s", s["shipment_ref"])
        else:
            logger.info("Shipment %s already exists, skipping", s["shipment_ref"])


async def seed_channel_messages(
    session: AsyncSession,
    channels: list[Channel],
    users: list[User],
) -> None:
    user_by_key = {
        "dispatcher": next(u for u in users if "dispatcher" in u.email),
        "driver": next(u for u in users if "driver" in u.email),
        "akash": next(u for u in users if "akash" in u.email),
    }
    for channel in channels:
        # Skip if this channel already has messages (idempotent).
        count_result = await session.execute(
            select(func.count()).where(Message.channel_id == channel.id)
        )
        if (count_result.scalar() or 0) > 0:
            logger.info("#%s already has messages, skipping", channel.name)
            continue

        msgs = CHANNEL_MESSAGES.get(channel.name, [])
        for sender_key, content, hours_ago in msgs:
            session.add(Message(
                channel_id=channel.id,
                sender_id=user_by_key[sender_key].id,
                content=content,
                created_at=_t(hours_ago),
            ))
        logger.info("Seeded %d messages into #%s", len(msgs), channel.name)


async def seed_dm_messages(
    session: AsyncSession,
    users: list[User],
) -> None:
    user_by_key = {
        "dispatcher": next(u for u in users if "dispatcher" in u.email),
        "driver": next(u for u in users if "driver" in u.email),
        "akash": next(u for u in users if "akash" in u.email),
    }

    for (key_a, key_b), messages in DM_MESSAGES.items():
        user_a = user_by_key[key_a]
        user_b = user_by_key[key_b]
        min_id, max_id = sorted([user_a.id, user_b.id])
        dm_name = f"dm_{min_id}_{max_id}"

        # Find or create the DM channel.
        result = await session.execute(
            select(Channel).where(Channel.name == dm_name, Channel.is_dm == True)  # noqa: E712
        )
        dm_channel = result.scalar_one_or_none()
        if dm_channel is None:
            dm_channel = Channel(name=dm_name, is_dm=True)
            session.add(dm_channel)
            await session.flush()
            # Create both memberships.
            session.add(Membership(user_id=user_a.id, channel_id=dm_channel.id))
            session.add(Membership(user_id=user_b.id, channel_id=dm_channel.id))
            logger.info("Created DM channel %s (id=%d)", dm_name, dm_channel.id)

        # Skip messages if already seeded.
        count_result = await session.execute(
            select(func.count()).where(Message.channel_id == dm_channel.id)
        )
        if (count_result.scalar() or 0) > 0:
            logger.info("DM %s already has messages, skipping", dm_name)
            continue

        for sender_key, content, hours_ago in messages:
            session.add(Message(
                channel_id=dm_channel.id,
                sender_id=user_by_key[sender_key].id,
                content=content,
                created_at=_t(hours_ago),
            ))
        logger.info("Seeded %d messages into DM %s", len(messages), dm_name)


async def main() -> None:
    async with async_session_factory() as session:
        channels = await seed_channels(session)
        users = await seed_users(session)
        await seed_memberships(session, users, channels)
        await seed_shipments(session)
        await seed_channel_messages(session, channels, users)
        await seed_dm_messages(session, users)
        await session.commit()
    logger.info("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
