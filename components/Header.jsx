"use client";

import {
  Show,
  SignInButton,
  SignedIn,
  SignedOut,
  UserButton,
  useUser,
  useAuth,
  Protect,
} from "@clerk/nextjs";
import Link from "next/link";
import Logo from "./Logo";

const IconAdmin = () => {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 448 512"
      fill="currentColor"
    >
      <path d="M96 128a128 128 0 1 0 256 0A128 128 0 1 0 96 128zm94.5 200.2l18.6 31L175.8 483.1l-36-146.9c-2-8.1-9.8-13.4-17.9-11.3C51.9 342.4 0 405.8 0 481.3c0 17 13.8 30.7 30.7 30.7l131.7 0c0 0 0 0 .1 0l5.5 0 112 0 5.5 0c0 0 0 0 .1 0l131.7 0c17 0 30.7-13.8 30.7-30.7c0-75.5-51.9-138.9-121.9-156.4c-8.1-2-15.9 3.3-17.9 11.3l-36 146.9L238.9 359.2l18.6-31c6.4-10.7-1.3-24.2-13.7-24.2L224 304l-19.7 0c-12.4 0-20.1 13.6-13.7 24.2z" />
    </svg>
  );
};

const IconPortal = () => {
  return (
    <svg className="size-5 fill-secondary" viewBox="0 0 512.001 512.001">
      <g strokeWidth="0"></g>
      <g strokeLinecap="round" strokeLinejoin="round"></g>
      <g>
        <path
          className="fill:#FFC61B;"
          d="M303.28,431.468H49.881C22.377,431.468,0,409.093,0,381.587V121.492 c0-27.504,22.377-49.881,49.881-49.881h354.711c27.506,0,49.883,22.377,49.883,49.881v76.227c0,8.119-6.583,14.7-14.7,14.7 c-8.118,0-14.7-6.581-14.7-14.7v-76.227c0-11.293-9.188-20.481-20.482-20.481H49.881c-11.293,0-20.481,9.188-20.481,20.481v260.096 c0,11.293,9.188,20.481,20.481,20.481H303.28c8.118,0,14.7,6.581,14.7,14.7S311.398,431.468,303.28,431.468z"
        ></path>{" "}
        <path
          className="fill:#FEE187"
          d="M123.802,89.498l-108.22,108.22l-0.882-0.882v-75.345c0-19.429,15.751-35.181,35.181-35.181h70.734 L123.802,89.498z"
        ></path>{" "}
        <path
          className="fill:#FFC61B;"
          d="M15.582,212.418c-0.001,0-0.001,0-0.003,0c-3.9,0-7.64-1.551-10.396-4.31l-0.882-0.882 C1.548,204.47,0,200.733,0,196.836v-75.344c0-27.504,22.377-49.881,49.881-49.881h70.734c3.897,0,7.635,1.548,10.392,4.303 l3.189,3.187c2.758,2.756,4.309,6.496,4.309,10.396c0,3.899-1.549,7.638-4.306,10.396L25.977,208.113 C23.219,210.869,19.481,212.418,15.582,212.418z M49.881,101.012c-11.293,0-20.481,9.188-20.481,20.481v41.618l62.099-62.099 L49.881,101.012L49.881,101.012z"
        ></path>{" "}
        <path
          className="fill:#FEE187;"
          d="M404.592,86.31c19.429,0,35.181,15.751,35.181,35.181v77.086l-0.882-0.859L330.67,89.498l3.187-3.187 h70.735V86.31z"
        ></path>{" "}
        <path
          className="fill:#FFC61B;"
          d="M439.776,213.278c-3.756,0-7.45-1.439-10.251-4.162l-0.883-0.857 c-0.05-0.049-0.098-0.097-0.148-0.146L320.274,99.893c-5.74-5.74-5.74-15.049,0-20.789l3.187-3.187 c2.758-2.756,6.496-4.306,10.395-4.306h70.734c27.506,0,49.883,22.377,49.883,49.881v77.086c0,5.908-3.537,11.241-8.979,13.542 C443.648,212.898,441.704,213.278,439.776,213.278z M362.972,101.012l62.1,62.1v-41.62c0-11.293-9.188-20.481-20.482-20.481 L362.972,101.012L362.972,101.012z"
        ></path>{" "}
        <path
          className="fill:#FEE187;"
          d="M123.802,413.581l-3.187,3.187H49.881c-19.429,0-35.181-15.75-35.181-35.181v-75.345l0.882-0.882 L123.802,413.581z"
        ></path>{" "}
        <g>
          {" "}
          <path
            className="fill:#FFC61B;"
            d="M120.615,431.468H49.881C22.377,431.468,0,409.093,0,381.587v-75.345 c0-3.897,1.548-7.634,4.301-10.39l0.882-0.882c2.756-2.758,6.496-4.309,10.396-4.31c0.001,0,0.001,0,0.003,0 c3.899,0,7.637,1.549,10.395,4.306l108.221,108.221c2.756,2.756,4.306,6.496,4.306,10.396c0,3.9-1.549,7.638-4.307,10.395 l-3.189,3.187C128.252,429.92,124.512,431.468,120.615,431.468z M29.401,339.969v41.62c0,11.293,9.188,20.481,20.481,20.481h41.621 L29.401,339.969z"
          ></path>{" "}
          <path
            className="fill:#FFC61B;"
            d="M290.229,101.012c-8.118,0-14.7-6.581-14.7-14.7V50.618h-96.583v35.694 c0,8.119-6.583,14.7-14.7,14.7s-14.7-6.581-14.7-14.7V35.917c0-8.119,6.583-14.7,14.7-14.7h125.983c8.118,0,14.7,6.581,14.7,14.7 v50.394C304.929,94.429,298.347,101.012,290.229,101.012z"
          ></path>{" "}
          <path
            className="fill:#FFC61B;"
            d="M360.61,450.38c-29.646,0-57.518-11.544-78.481-32.507c-43.273-43.275-43.273-113.685,0-156.96 c20.963-20.963,48.835-32.507,78.481-32.507c29.646,0,57.518,11.546,78.481,32.508c43.273,43.273,43.273,113.685,0,156.959 C418.128,438.836,390.256,450.38,360.61,450.38z M360.61,257.806c-21.792,0-42.281,8.487-57.69,23.895 c-31.812,31.812-31.812,83.572,0,115.382c15.409,15.409,35.898,23.895,57.69,23.895s42.281-8.485,57.69-23.895 c31.812-31.81,31.812-83.57,0-115.38C402.89,266.292,382.402,257.806,360.61,257.806z"
          ></path>{" "}
          <path
            className="fill:#FFC61B;"
            d="M497.3,490.784c-3.763,0-7.524-1.435-10.395-4.306L418.3,417.874c-5.74-5.741-5.74-15.049,0-20.791 c5.742-5.74,15.047-5.74,20.791,0l68.605,68.605c5.74,5.74,5.74,15.049,0,20.791C504.823,489.348,501.063,490.784,497.3,490.784z"
          ></path>{" "}
        </g>{" "}
      </g>
    </svg>
  );
};

export default function Header() {
  //  const { has, isLoaded } = useAuth();
  const { user } = useUser();

  //  if (!isLoaded) {
  //    return <span></span>;
  //  }

  //  const isAdmin = has({ role: "org:admin" });
  //const isAdmin = has({ permission: "org:admin:noble_trading_app" });

  return (
    <div className="navbar bg-base-100 shadow-sm">
      <div className="flex-1 px-2">
        <Logo size={36} padding={0} />
        <Link href="/" className="btn btn-ghost text-xl uppercase px-2">
          Noble Trader Risk Manager
        </Link>
      </div>
      <div className=" flex-none uppercase ml-2 px-2">
        <Show when="signed-out">
          <SignInButton forceRedirectUrl="/riskmanager">
            <div className="btn btn-secondary rounded-full font-bold uppercase">
              Login
            </div>
          </SignInButton>
        </Show>
        <Show when="signed-in">
          {/* <UserButton />*/}
          <ul className="menu menu-horizontal px-1">
            <li className="px-2">
              <div className="btn btn-secondary text-sm border-rounded">
                <Link href="/history">History</Link>
              </div>
            </li>
            <li>
              <UserButton>
                {/* user?.publicMetadata.role == "admin" &&) */}
                <UserButton.MenuItems>
                  {user?.publicMetadata.role == "admin" && (
                    <UserButton.Link
                      label="Admin"
                      labelIcon={<IconAdmin />}
                      href="/admin"
                      forceRedirectUrl="/admin"
                    />
                  )}
                  {/*  <UserButton.Link
                label="Portal"
                labelIcon={<IconPortal />}
                href="/portal"
            />*/}
                  <UserButton.Action label="manageAccount" />
                </UserButton.MenuItems>
              </UserButton>
            </li>
          </ul>
        </Show>
      </div>
    </div>
  );
}
