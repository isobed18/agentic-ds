/** Which connector arrows pulse while a pipeline runs.
 *
 * One rule, stated once: an arrow pulses for the group it points *into*. The
 * pulse means "work is arriving here", which is why the very first arrow --
 * the edge out of the accepted plan -- has always lit on group 0 running.
 *
 * The arrows between groups did not follow it. Each one lit when the group
 * *behind* it was running as well as when the group ahead of it was, so a
 * running group N pulsed both the arrow into it and the arrow out of it and
 * the canvas showed two animations for one piece of work (#313).
 *
 * Kept out of the component because the property that matters -- at most one
 * arrow at a time -- is a statement about the whole row, and a single arrow's
 * props cannot express it.
 */
import type { WorkflowNode } from "../lib/api";

/** Whether work is arriving at a group, as opposed to having reached it. */
function receiving(status: WorkflowNode["status"] | undefined): boolean {
  return status === "running" || status === "retry";
}

/**
 * One flag per arrow, where arrow `i` is the edge pointing into group `i`.
 *
 * There is one more arrow than there are gaps between groups: arrow 0 comes
 * out of the plan node, so the returned array is the same length as
 * `groupStatuses`.
 */
export function activeArrows(groupStatuses: (WorkflowNode["status"] | undefined)[]): boolean[] {
  return groupStatuses.map((status) => receiving(status));
}
