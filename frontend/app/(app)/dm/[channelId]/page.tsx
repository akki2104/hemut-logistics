import ChannelView from "@/components/ChannelView";

export default function DMPage({ params }: { params: { channelId: string } }) {
  return <ChannelView channelId={Number(params.channelId)} />;
}
