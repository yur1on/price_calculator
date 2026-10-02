"""Presentation-only contact data for printed CRM documents."""
from urllib.parse import urlsplit, urlunsplit

from django.urls import reverse


def print_status_url(website, request):
    path = reverse("repair_status")
    value = (website or "").strip()
    if value:
        candidate = value if "://" in value else "https://" + value
        try:
            parsed = urlsplit(candidate)
            # A printed website is an origin, not a replacement for the public route.
            if (parsed.scheme in {"http", "https"} and parsed.hostname
                    and not parsed.username and not parsed.password
                    and not any(char.isspace() for char in parsed.netloc)):
                parsed.port  # Reject malformed ports instead of breaking document rendering.
                return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        except ValueError:
            pass
    return request.build_absolute_uri(path)


def document_contact_context(configuration, request):
    return {
        "document_contacts": [value.strip() for value in (
            configuration.phone, configuration.website, configuration.email,
        ) if value and value.strip()],
        "document_status_url": print_status_url(configuration.website, request),
    }
