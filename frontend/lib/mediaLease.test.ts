import { expect, it, vi } from "vitest";
import { MediaLease } from "./mediaLease";

it("rejects and stops permission granted after cancel, without losing a newer lease", () => {
  const lease=new MediaLease(), stop=vi.fn();
  const first=lease.begin()!;expect(lease.begin()).toBeNull();lease.cancel();
  const second=lease.begin()!;
  expect(lease.accept(first,{getTracks:()=>[{stop}]} as unknown as MediaStream)).toBe(false);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(lease.accept(second,{getTracks:()=>[]} as unknown as MediaStream)).toBe(true);
  expect(lease.begin()).not.toBeNull();
});
