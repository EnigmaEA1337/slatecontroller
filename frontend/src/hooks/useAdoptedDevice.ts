/**
 * Tells callers whether ANY adopted device exists.
 *
 * Most of the controller's UI is meaningful only once at least one Slate
 * has been adopted — dashboards, security scans, profile apply, Wi-Fi
 * management all probe the device. Before adoption they show empty
 * cards / time out / surface confusing errors.
 *
 * The hook polls `/api/devices` and surfaces a single boolean so guards
 * (Layout, route wrappers, sidebar) can hide / redirect / no-op until
 * the user finishes their first adoption.
 *
 * Refetch is fast (10s) because adoption is a synchronous flow : the
 * user clicks Adopt, the report comes back, and the next poll picks up
 * the new ``status=adopted`` row instantly.
 */

import { useQuery } from "@tanstack/react-query";
import { listDevices } from "@/api/devices";

export function useAdoptedDevice() {
  const q = useQuery({
    queryKey: ["devices", "list"],
    queryFn: listDevices,
    staleTime: 10_000,
    refetchInterval: 10_000,
  });

  const devices = q.data ?? [];
  const adoptedDevices = devices.filter((d) => d.status === "adopted");

  return {
    isLoading: q.isLoading,
    hasAnyDevice: devices.length > 0,
    hasAdopted: adoptedDevices.length > 0,
    adoptedDevices,
    allDevices: devices,
  };
}
