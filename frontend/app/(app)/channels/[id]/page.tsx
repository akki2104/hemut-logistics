import ChannelView from "@/components/ChannelView";

export default function ChannelPage({ params }: { params: { id: string } }) {
  return <ChannelView channelId={Number(params.id)} />;
}
