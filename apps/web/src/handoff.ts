// USB handoff failures: the run keeps a stable code as `current_step` and a
// stable English `message` (apps/api/app/services/disk_handoff.py). Pure (node:test).

export const HANDOFF_CODES = [
  "usb_device_not_found",
  "usb_location_not_in_proxmox",
  "disk_not_plugged",
  "usb_device_ambiguous",
] as const;
export type HandoffCode = (typeof HANDOFF_CODES)[number];

// Must match the API constants (checked by apps/api/tests/test_usb_location_handoff.py).
export const HANDOFF_MESSAGES: Record<string, HandoffCode> = {
  "Disk not found in the Proxmox USB device list.": "usb_device_not_found",
  "The disk's USB port is not in the Proxmox USB device list.": "usb_location_not_in_proxmox",
  "The disk is no longer plugged into the Proxmox host.": "disk_not_plugged",
  "Several USB devices could be this disk. Update the host agent (it reports the exact USB port), or unplug the other USB storage devices.":
    "usb_device_ambiguous",
};

type HandoffTexts = Record<HandoffCode, { step: string; message: string }>;

export function handoffStepLabel(step: string | null, texts: HandoffTexts): string | null {
  return step && (HANDOFF_CODES as readonly string[]).includes(step) ? texts[step as HandoffCode].step : null;
}

export function handoffMessage(message: string | null, texts: HandoffTexts): string | null {
  const code = message ? HANDOFF_MESSAGES[message] : undefined;
  return code ? texts[code].message : null;
}
