// Hook that wraps the PinConfirmModal lifecycle so a single line gates
// any sensitive action.
//
// Usage :
//
//     const pin = usePinConfirm({
//       title: "Confirmer la suppression",
//       description: "Cette action est irréversible.",
//       onConfirmed: () => doDangerousThing(),
//     });
//
//     return (
//       <>
//         <button onClick={pin.request}>supprimer</button>
//         {pin.modal}
//       </>
//     );
//
// The hook owns the open state ; the consumer just renders `pin.modal`
// somewhere in the tree (typically at the root of the component) and
// calls `pin.request()` when ready to ask.

import { useCallback, useState } from "react";

import PinConfirmModal from "@/components/PinConfirmModal";

interface Options {
  title?: string;
  description?: string;
  scope?: string;
  onConfirmed: () => void;
}

export function usePinConfirm({
  title,
  description,
  scope,
  onConfirmed,
}: Options) {
  const [open, setOpen] = useState(false);
  const request = useCallback(() => setOpen(true), []);
  const close = useCallback(() => setOpen(false), []);
  const modal = (
    <PinConfirmModal
      open={open}
      onClose={close}
      onConfirmed={onConfirmed}
      title={title}
      description={description}
      scope={scope}
    />
  );
  return { request, close, modal, open };
}
