const channels = ['#onboarding', '#access', '#devices', '#systems', '#applications']
const privateChannels = ['@onboarding-agent', '@device-agent']

export function ChannelsView({ selected, onSelect }: { selected: string; onSelect: (channel: string) => void }) {
  return <section className="channels"><h2>Channels</h2>{channels.map((channel) => <button className={selected === channel ? 'selected' : ''} onClick={() => onSelect(channel)} key={channel}>{channel}</button>)}<h3>Private</h3>{privateChannels.map((channel) => <button className={selected === channel ? 'selected' : ''} onClick={() => onSelect(channel)} key={channel}>{channel}</button>)}</section>
}
