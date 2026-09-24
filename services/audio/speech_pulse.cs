using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Speech.AudioFormat;

// Only the assistant's PCM passes through this stream. No microphone/loopback.
// Audio is played in bounded blocks; completed blocks supply UI telemetry.
public sealed class ValeraSpeechAudio : Stream
{
    [StructLayout(LayoutKind.Sequential, Pack = 2)]
    private struct WaveFormat
    {
        public ushort Tag, Channels;
        public uint Rate, BytesPerSecond;
        public ushort Alignment, Bits, Extra;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct Header
    {
        public IntPtr Data;
        public uint Length, Recorded;
        public UIntPtr User;
        public uint Flags, Loops;
        public IntPtr Next;
        public UIntPtr Reserved;
    }
    private sealed class Packet
    {
        public IntPtr Data, Header;
        public byte[] Pcm;
    }
    [DllImport("winmm.dll")] private static extern uint waveOutOpen(out IntPtr device, uint id, ref WaveFormat format, IntPtr callback, IntPtr instance, uint flags);
    [DllImport("winmm.dll")] private static extern uint waveOutPrepareHeader(IntPtr device, IntPtr header, uint size);
    [DllImport("winmm.dll")] private static extern uint waveOutWrite(IntPtr device, IntPtr header, uint size);
    [DllImport("winmm.dll")] private static extern uint waveOutUnprepareHeader(IntPtr device, IntPtr header, uint size);
    [DllImport("winmm.dll")] private static extern uint waveOutReset(IntPtr device);
    [DllImport("winmm.dll")] private static extern uint waveOutClose(IntPtr device);

    private IntPtr device;
    private readonly Queue<Packet> pending = new Queue<Packet>();
    private readonly byte[] block = new byte[2048]; // 46 ms, mono PCM16 / 22050 Hz.
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private readonly uint headerSize = (uint)Marshal.SizeOf(typeof(Header));
    private int buffered;
    private long position, lastTelemetry = -1000;
    private bool closed;
    public static SpeechAudioFormatInfo Format
    {
        get { return new SpeechAudioFormatInfo(22050, AudioBitsPerSample.Sixteen, AudioChannel.Mono); }
    }
    public ValeraSpeechAudio()
    {
        WaveFormat format = new WaveFormat { Tag = 1, Channels = 1, Rate = 22050,
            BytesPerSecond = 44100, Alignment = 2, Bits = 16 };
        Check(waveOutOpen(out device, 0xffffffff, ref format, IntPtr.Zero, IntPtr.Zero, 0));
    }
    private static void Check(uint result)
    {
        if (result != 0) throw new IOException("PCM output error: " + result);
    }
    public override bool CanRead { get { return false; } }
    public override bool CanSeek { get { return false; } }
    public override bool CanWrite { get { return !closed; } }
    public override long Length { get { return position; } }
    public override long Position { get { return position; } set { throw new NotSupportedException(); } }
    public override int Read(byte[] b, int o, int n) { throw new NotSupportedException(); }
    public override long Seek(long o, SeekOrigin s) { throw new NotSupportedException(); }
    public override void SetLength(long n) { throw new NotSupportedException(); }
    public override void Write(byte[] data, int offset, int count)
    {
        if (closed) throw new ObjectDisposedException("ValeraSpeechAudio");
        if (data == null || offset < 0 || count < 0 || offset > data.Length - count)
            throw new ArgumentException("Invalid PCM buffer");
        while (count > 0)
        {
            int take = Math.Min(count, block.Length - buffered);
            Buffer.BlockCopy(data, offset, block, buffered, take);
            buffered += take; offset += take; count -= take; position += take;
            if (buffered == block.Length) Submit();
        }
    }
    private void Submit()
    {
        if (buffered == 0) return;
        if ((buffered & 1) != 0) throw new IOException("Incomplete PCM16 sample");
        // Backpressure limits queued audio to three blocks (about 140 ms).
        while (pending.Count >= 3) CompleteFirst();
        Packet packet = new Packet { Pcm = new byte[buffered] };
        Buffer.BlockCopy(block, 0, packet.Pcm, 0, buffered);
        packet.Data = Marshal.AllocHGlobal(buffered);
        bool prepared = false;
        try
        {
            packet.Header = Marshal.AllocHGlobal((int)headerSize);
            Marshal.Copy(packet.Pcm, 0, packet.Data, buffered);
            Header header = new Header { Data = packet.Data, Length = (uint)buffered };
            Marshal.StructureToPtr(header, packet.Header, false);
            Check(waveOutPrepareHeader(device, packet.Header, headerSize));
            prepared = true;
            Check(waveOutWrite(device, packet.Header, headerSize));
            pending.Enqueue(packet);
            buffered = 0;
        }
        catch
        {
            if (prepared) waveOutUnprepareHeader(device, packet.Header, headerSize);
            if (packet.Header != IntPtr.Zero) Marshal.FreeHGlobal(packet.Header);
            Marshal.FreeHGlobal(packet.Data);
            throw;
        }
    }
    private void CompleteFirst()
    {
        Packet packet = pending.Peek();
        long deadline = clock.ElapsedMilliseconds + 3000;
        while ((((Header)Marshal.PtrToStructure(packet.Header, typeof(Header))).Flags & 1) == 0)
        {
            if (clock.ElapsedMilliseconds > deadline) throw new IOException("PCM playback timeout");
            Thread.Sleep(4);
        }
        Check(waveOutUnprepareHeader(device, packet.Header, headerSize));
        pending.Dequeue();
        Marshal.FreeHGlobal(packet.Header);
        Marshal.FreeHGlobal(packet.Data);
        if (clock.ElapsedMilliseconds - lastTelemetry < 65) return;
        lastTelemetry = clock.ElapsedMilliseconds;
        try { Console.Out.WriteLine(Describe(packet.Pcm)); }
        catch (IOException) { /* Visualization never owns the voice lifetime. */ }
    }
    // 32 signed block averages, fixed gain, no per-frame normalization.
    // These are a reduced representation of real PCM, not a sine template.
    public static string Describe(byte[] pcm)
    {
        int count = pcm.Length / 2;
        double energy = 0;
        for (int i = 0; i < count; ++i)
        {
            double value = (short)(pcm[2 * i] | pcm[2 * i + 1] << 8) / 32768.0;
            energy += value * value;
        }
        int level = count == 0 ? 0 : (int)Math.Min(100, Math.Sqrt(energy / count) * 400);
        StringBuilder json = new StringBuilder("{\"event\":\"speech_audio\",\"level\":");
        json.Append(level).Append(",\"samples\":[");
        for (int bin = 0; bin < 32; ++bin)
        {
            int start = bin * count / 32, end = (bin + 1) * count / 32;
            double total = 0;
            for (int i = start; i < end; ++i) total += (short)(pcm[2 * i] | pcm[2 * i + 1] << 8);
            int value = end == start ? 0 : (int)Math.Round(total / (end - start) * 300 / 32768.0);
            if (bin != 0) json.Append(',');
            json.Append(Math.Max(-100, Math.Min(100, value)));
        }
        return json.Append("]}").ToString();
    }
    public override void Flush()
    {
        if (closed) return;
        Submit();
        while (pending.Count > 0) CompleteFirst();
    }
    protected override void Dispose(bool disposing)
    {
        if (closed) return;
        closed = true;
        waveOutReset(device);
        while (pending.Count > 0)
        {
            Packet packet = pending.Dequeue();
            // Do not free memory still owned by a driver if reset/unprepare fails.
            if (waveOutUnprepareHeader(device, packet.Header, headerSize) == 0)
            {
                Marshal.FreeHGlobal(packet.Header);
                Marshal.FreeHGlobal(packet.Data);
            }
        }
        waveOutClose(device);
        device = IntPtr.Zero;
        base.Dispose(disposing);
    }
}
