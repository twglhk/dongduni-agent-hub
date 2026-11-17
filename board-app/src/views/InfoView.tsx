import { DiskCard } from '../components/DiskCard';
import { MachineCard } from '../components/MachineCard';
import { Empty, Section } from '../components/Section';
import { XSections } from '../components/XHost';
import { useL } from '../i18n/context';
import { useBoard } from '../state/board';

// This machine: load, CPU, memory and swap graded against its own size, the Claude plan when the workspace tracks it,
// and why tasks wait. The disk card shows when the tower reports disks.
export function InfoView() {
  const { L } = useL();
  const { mach } = useBoard();
  if (!mach?.machine) return <Empty>{L.empty.loading}</Empty>;
  const disks = mach.machine.disks || [];
  return (
    <>
      <Section title={L.mc.title}><MachineCard j={mach} /></Section>
      {disks.length > 0 && <Section title={L.disk.title}><DiskCard disks={disks} /></Section>}
      <XSections tab="info" />
    </>
  );
}
