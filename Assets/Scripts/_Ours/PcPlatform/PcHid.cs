#if !SWITCH
// PC platform layer: nn::hid Npad on the PC (HANDOFF §11.7 "Input"). NOT shipped code.
//
// The emulated console has ONE controller: NpadId.No1 with style FullKey (a Pro Controller, Ryujinx's default player 1), always
// connected; Handheld and the other ids report style None. Its state is built every Unity frame from three sources, OR-ed:
//   1. the keyboard, with Ryujinx's default key bindings (so the editor plays like the reference emulator):
//        left stick W/A/S/D (click F), d-pad arrow keys, Minus = '-', L = E, ZL = Q,
//        right stick I/J/K/L (click H), A = Z, B = X, X = C, Y = V, Plus = '=' / keypad '+', R = U, ZR = O;
//   2. XInput pads (PcXInput, s41), mapped by POSITION as Ryujinx does: Switch A = pad B, B = A, X = Y, Y = X, L/R = LB/RB,
//      ZL/ZR = LT/RT, Minus = Back, Plus = Start, stick clicks = LS/RS. Off Windows: Unity's legacy Input joystick 1 with the
//      "PcPad.*" axes of ProjectSettings/InputManager.asset (the s34-s40 path);
//   3. a scripted input queue (PcInput) that the editor play driver loads for automated runs.
// Stick values are -32767..32767 (nn::hid AnalogStickMax); the stick-direction buttons (StickLLeft ...) are set past half
// deflection, as Ryujinx's NpadDevices does (StickButtonThreshold = short.MaxValue / 2).
using System;
using nn.hid;
using UnityEngine;

namespace PcPlatform
{
    public static class PcHid
    {
        const int StickMax = 32767;
        const int StickButtonThreshold = short.MaxValue / 2;

        static NpadStyle supportedStyles = NpadStyle.FullKey | NpadStyle.Handheld | NpadStyle.JoyDual | NpadStyle.JoyLeft | NpadStyle.JoyRight;
        static int lastFrame = -1;
        static long samplingNumber;
        static NpadButton buttons;
        static AnalogStickState stickL, stickR;
        static bool axesChecked, axesPresent;

        // nn::hid keeps the last 16 samples per pad (NpadStateCountMax); GetStates returns them newest first.
        const int HistoryMax = 16;
        static readonly long[] histSampling = new long[HistoryMax];
        static readonly NpadButton[] histButtons = new NpadButton[HistoryMax];
        static readonly AnalogStickState[] histL = new AnalogStickState[HistoryMax];
        static readonly AnalogStickState[] histR = new AnalogStickState[HistoryMax];
        static int histHead, histCount;

        public static void Initialize()
        {
            PcConfig.Log("hid: one Pro Controller (Npad No1, FullKey): keyboard (Ryujinx default bindings) + XInput pad + scripted input");
        }

        public static void SetSupportedStyleSet(NpadStyle styles) { supportedStyles = styles; }

        public static NpadStyle GetSupportedStyleSet() { return supportedStyles; }

        public static NpadStyle GetStyleSet(NpadId id)
        {
            return id == NpadId.No1 && (supportedStyles & NpadStyle.FullKey) != 0 ? NpadStyle.FullKey : NpadStyle.None;
        }

        // The state of `id` read as `style`: the controller only answers for its own id and style; anything else reads as a
        // disconnected pad (zeroed, attributes None) with the sampling number still counting, as the console's shared memory does.
        public static void Read(NpadId id, NpadStyle style, out long sampling, out NpadButton outButtons, out AnalogStickState l,
            out AnalogStickState r, out NpadAttribute attributes)
        {
            Sample();
            sampling = samplingNumber;
            if (id == NpadId.No1 && style == NpadStyle.FullKey && (supportedStyles & NpadStyle.FullKey) != 0)
            {
                outButtons = buttons;
                l = stickL;
                r = stickR;
                attributes = NpadAttribute.IsConnected | NpadAttribute.IsWired;
            }
            else
            {
                outButtons = NpadButton.None;
                l = new AnalogStickState();
                r = new AnalogStickState();
                attributes = NpadAttribute.None;
            }
        }

        public delegate void StateSink(int index, long sampling, NpadButton buttons, AnalogStickState l, AnalogStickState r, NpadAttribute attributes);

        // GetStates: up to `count` samples of `id` read as `style`, newest first; returns how many were written.
        public static int ReadHistory(NpadId id, NpadStyle style, int count, StateSink sink)
        {
            Sample();
            int n = Math.Min(count, histCount);
            bool mine = id == NpadId.No1 && style == NpadStyle.FullKey && (supportedStyles & NpadStyle.FullKey) != 0;
            for (int i = 0; i < n; i++)
            {
                int k = (histHead - i + HistoryMax) % HistoryMax;
                if (mine)
                    sink(i, histSampling[k], histButtons[k], histL[k], histR[k], NpadAttribute.IsConnected | NpadAttribute.IsWired);
                else
                    sink(i, histSampling[k], NpadButton.None, new AnalogStickState(), new AnalogStickState(), NpadAttribute.None);
            }
            return n;
        }

        // Once per Unity frame (the game polls the pad once per Sequencer update).
        static void Sample()
        {
            int frame = Time.frameCount;
            if (frame == lastFrame)
                return;
            lastFrame = frame;
            samplingNumber++;

            NpadButton b = NpadButton.None;
            int lx = 0, ly = 0, rx = 0, ry = 0;

            // s34: a scripted run ignores the physical keyboard / pad for the whole session - the editor window takes focus when the
            // play driver starts it, and keys typed anywhere on the machine then drove the game (C = X opened the menu, Z = A, X = B).
            if (PcInput.Scripted)
                goto script;

            // 1. keyboard
            if (Input.GetKey(KeyCode.Z)) b |= NpadButton.A;
            if (Input.GetKey(KeyCode.X)) b |= NpadButton.B;
            if (Input.GetKey(KeyCode.C)) b |= NpadButton.X;
            if (Input.GetKey(KeyCode.V)) b |= NpadButton.Y;
            if (Input.GetKey(KeyCode.F)) b |= NpadButton.StickL;
            if (Input.GetKey(KeyCode.H)) b |= NpadButton.StickR;
            if (Input.GetKey(KeyCode.E)) b |= NpadButton.L;
            if (Input.GetKey(KeyCode.U)) b |= NpadButton.R;
            if (Input.GetKey(KeyCode.Q)) b |= NpadButton.ZL;
            if (Input.GetKey(KeyCode.O)) b |= NpadButton.ZR;
            if (Input.GetKey(KeyCode.Equals) || Input.GetKey(KeyCode.KeypadPlus)) b |= NpadButton.Plus;
            if (Input.GetKey(KeyCode.Minus) || Input.GetKey(KeyCode.KeypadMinus)) b |= NpadButton.Minus;
            if (Input.GetKey(KeyCode.LeftArrow)) b |= NpadButton.Left;
            if (Input.GetKey(KeyCode.UpArrow)) b |= NpadButton.Up;
            if (Input.GetKey(KeyCode.RightArrow)) b |= NpadButton.Right;
            if (Input.GetKey(KeyCode.DownArrow)) b |= NpadButton.Down;
            lx += (Input.GetKey(KeyCode.D) ? StickMax : 0) - (Input.GetKey(KeyCode.A) ? StickMax : 0);
            ly += (Input.GetKey(KeyCode.W) ? StickMax : 0) - (Input.GetKey(KeyCode.S) ? StickMax : 0);
            rx += (Input.GetKey(KeyCode.L) ? StickMax : 0) - (Input.GetKey(KeyCode.J) ? StickMax : 0);
            ry += (Input.GetKey(KeyCode.I) ? StickMax : 0) - (Input.GetKey(KeyCode.K) ? StickMax : 0);

            // 2. XInput pads, positional mapping (s41: XInputGetState in PcXInput; s34-s40 read Unity's "Joystick 1", which is any
            //    HID joystick with its own axis numbers - a DualShock 4 held "right stick left" through its resting L2 trigger)
#if UNITY_STANDALONE_WIN || UNITY_EDITOR_WIN
            PcXInput.Read(ref b, ref lx, ref ly, ref rx, ref ry);
#else
            if (Input.GetKey(KeyCode.Joystick1Button1)) b |= NpadButton.A;
            if (Input.GetKey(KeyCode.Joystick1Button0)) b |= NpadButton.B;
            if (Input.GetKey(KeyCode.Joystick1Button3)) b |= NpadButton.X;
            if (Input.GetKey(KeyCode.Joystick1Button2)) b |= NpadButton.Y;
            if (Input.GetKey(KeyCode.Joystick1Button4)) b |= NpadButton.L;
            if (Input.GetKey(KeyCode.Joystick1Button5)) b |= NpadButton.R;
            if (Input.GetKey(KeyCode.Joystick1Button6)) b |= NpadButton.Minus;
            if (Input.GetKey(KeyCode.Joystick1Button7)) b |= NpadButton.Plus;
            if (Input.GetKey(KeyCode.Joystick1Button8)) b |= NpadButton.StickL;
            if (Input.GetKey(KeyCode.Joystick1Button9)) b |= NpadButton.StickR;
            if (AxesPresent())
            {
                if (Input.GetAxisRaw("PcPad.LT") > 0.5f) b |= NpadButton.ZL;
                if (Input.GetAxisRaw("PcPad.RT") > 0.5f) b |= NpadButton.ZR;
                float dx = Input.GetAxisRaw("PcPad.DX"), dy = Input.GetAxisRaw("PcPad.DY");
                if (dx < -0.5f) b |= NpadButton.Left;
                if (dx > 0.5f) b |= NpadButton.Right;
                if (dy > 0.5f) b |= NpadButton.Up;
                if (dy < -0.5f) b |= NpadButton.Down;
                lx += (int)(Mathf.Clamp(Input.GetAxisRaw("PcPad.LX"), -1f, 1f) * StickMax);
                ly += (int)(Mathf.Clamp(Input.GetAxisRaw("PcPad.LY"), -1f, 1f) * StickMax);
                rx += (int)(Mathf.Clamp(Input.GetAxisRaw("PcPad.RX"), -1f, 1f) * StickMax);
                ry += (int)(Mathf.Clamp(Input.GetAxisRaw("PcPad.RY"), -1f, 1f) * StickMax);
            }
#endif

            // 3. scripted input
            script:
            PcInput.Apply(ref b, ref lx, ref ly, ref rx, ref ry);

            stickL = new AnalogStickState { x = Clamp(lx), y = Clamp(ly) };
            stickR = new AnalogStickState { x = Clamp(rx), y = Clamp(ry) };
            if (stickL.x < -StickButtonThreshold) b |= NpadButton.StickLLeft;
            if (stickL.x > StickButtonThreshold) b |= NpadButton.StickLRight;
            if (stickL.y > StickButtonThreshold) b |= NpadButton.StickLUp;
            if (stickL.y < -StickButtonThreshold) b |= NpadButton.StickLDown;
            if (stickR.x < -StickButtonThreshold) b |= NpadButton.StickRLeft;
            if (stickR.x > StickButtonThreshold) b |= NpadButton.StickRRight;
            if (stickR.y > StickButtonThreshold) b |= NpadButton.StickRUp;
            if (stickR.y < -StickButtonThreshold) b |= NpadButton.StickRDown;
            buttons = b;

            histHead = (histHead + 1) % HistoryMax;
            histSampling[histHead] = samplingNumber;
            histButtons[histHead] = b;
            histL[histHead] = stickL;
            histR[histHead] = stickR;
            if (histCount < HistoryMax)
                histCount++;
        }

        static int Clamp(int v)
        {
            return v < -StickMax ? -StickMax : (v > StickMax ? StickMax : v);
        }

        static bool AxesPresent()
        {
            if (!axesChecked)
            {
                axesChecked = true;
                try
                {
                    Input.GetAxisRaw("PcPad.LX");
                    axesPresent = true;
                }
                catch (ArgumentException)
                {
                    axesPresent = false;
                    PcConfig.Log("hid: the PcPad.* axes are missing from InputManager.asset - pad sticks / triggers / d-pad disabled");
                }
            }
            return axesPresent;
        }
    }
}
#endif
