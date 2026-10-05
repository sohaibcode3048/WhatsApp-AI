"""Pydantic schemas for Meta WhatsApp Cloud API webhooks.

Covers all inbound message types:
  text, audio, image, document, video, sticker, location, contacts,
  interactive (button/list replies), and delivery status updates.
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Message content type schemas
# ---------------------------------------------------------------------------


class WhatsAppTextMessage(BaseModel):
    body: str


class WhatsAppAudioMessage(BaseModel):
    id: str
    mime_type: Optional[str] = None


class WhatsAppImageMessage(BaseModel):
    id: str
    mime_type: Optional[str] = None
    sha256: Optional[str] = None
    caption: Optional[str] = None


class WhatsAppDocumentMessage(BaseModel):
    id: str
    mime_type: Optional[str] = None
    sha256: Optional[str] = None
    filename: Optional[str] = None
    caption: Optional[str] = None


class WhatsAppVideoMessage(BaseModel):
    id: str
    mime_type: Optional[str] = None
    sha256: Optional[str] = None
    caption: Optional[str] = None


class WhatsAppStickerMessage(BaseModel):
    id: str
    mime_type: Optional[str] = None
    sha256: Optional[str] = None
    animated: bool = False


class WhatsAppLocationMessage(BaseModel):
    latitude: float
    longitude: float
    name: Optional[str] = None
    address: Optional[str] = None


class WhatsAppButtonReply(BaseModel):
    id: str
    title: str


class WhatsAppListReply(BaseModel):
    id: str
    title: str
    description: Optional[str] = None


class WhatsAppInteractiveMessage(BaseModel):
    """Interactive response — button reply or list reply from the customer."""

    type: str  # "button_reply" | "list_reply"
    button_reply: Optional[WhatsAppButtonReply] = None
    list_reply: Optional[WhatsAppListReply] = None


class WhatsAppReactionMessage(BaseModel):
    message_id: str
    emoji: str


# ---------------------------------------------------------------------------
# Incoming message envelope
# ---------------------------------------------------------------------------


class WhatsAppIncomingMessage(BaseModel):
    """Single message object inside a webhook value payload."""

    id: str
    from_: str = Field(alias="from")
    timestamp: str
    type: str = "text"

    # Populated based on 'type'
    text: Optional[WhatsAppTextMessage] = None
    audio: Optional[WhatsAppAudioMessage] = None
    image: Optional[WhatsAppImageMessage] = None
    document: Optional[WhatsAppDocumentMessage] = None
    video: Optional[WhatsAppVideoMessage] = None
    sticker: Optional[WhatsAppStickerMessage] = None
    location: Optional[WhatsAppLocationMessage] = None
    interactive: Optional[WhatsAppInteractiveMessage] = None
    reaction: Optional[WhatsAppReactionMessage] = None

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Status update (delivery / read receipts)
# ---------------------------------------------------------------------------


class WhatsAppStatusConversation(BaseModel):
    id: str
    expiration_timestamp: Optional[str] = None
    origin: Optional[Dict[str, Any]] = None


class WhatsAppStatusPricing(BaseModel):
    billable: Optional[bool] = None
    pricing_model: Optional[str] = None
    category: Optional[str] = None


class WhatsAppStatusUpdate(BaseModel):
    """Delivery / read receipt for an outbound message."""

    id: str                        # outbound message id
    recipient_id: str
    status: str                    # "sent" | "delivered" | "read" | "failed"
    timestamp: str
    conversation: Optional[WhatsAppStatusConversation] = None
    pricing: Optional[WhatsAppStatusPricing] = None
    errors: Optional[List[Dict[str, Any]]] = None


# ---------------------------------------------------------------------------
# Contact profile
# ---------------------------------------------------------------------------


class WhatsAppContactProfile(BaseModel):
    name: Optional[str] = None


class WhatsAppContact(BaseModel):
    wa_id: str
    profile: Optional[WhatsAppContactProfile] = None


# ---------------------------------------------------------------------------
# Webhook envelope
# ---------------------------------------------------------------------------


class WhatsAppMetadata(BaseModel):
    display_phone_number: Optional[str] = None
    phone_number_id: str


class WhatsAppValue(BaseModel):
    """'value' object inside a change entry.

    Contains either messages + contacts (inbound) or statuses (delivery receipts).
    """

    messaging_product: str = "whatsapp"
    metadata: WhatsAppMetadata
    contacts: Optional[List[WhatsAppContact]] = None
    messages: Optional[List[WhatsAppIncomingMessage]] = None
    statuses: Optional[List[WhatsAppStatusUpdate]] = None
    errors: Optional[List[Dict[str, Any]]] = None


class WhatsAppChange(BaseModel):
    value: WhatsAppValue
    field: str = "messages"


class WhatsAppEntry(BaseModel):
    id: str
    changes: List[WhatsAppChange]


class WhatsAppWebhookPayload(BaseModel):
    """Top-level Meta WhatsApp Cloud API webhook request body."""

    object: str = "whatsapp_business_account"
    entry: List[WhatsAppEntry]


# ---------------------------------------------------------------------------
# Outbound message helpers
# ---------------------------------------------------------------------------


class OutboundTextMessage(BaseModel):
    """Schema for sending a text message via Meta Cloud API."""

    to: str
    body: str
    preview_url: bool = False


class OutboundTemplateMessage(BaseModel):
    """Schema for sending a template message via Meta Cloud API."""

    to: str
    template_name: str
    language_code: str = "en_US"
    components: List[Dict[str, Any]] = Field(default_factory=list)
