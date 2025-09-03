//React
import { useEffect } from "react";

//Components
import {
    PeopleSettingsForm,
    SquareSkeleton,
    UserSettingsForm,
} from "@componentsReact";

//Hooks
import { useAuth } from "@hooks";

const Settings = () => {
    const { user, getUserData } = useAuth();

    useEffect(() => {
        getUserData();
    }, []);

    return (
        <div className="justify-items-center mt-2">
            <h3 className="text-3xl font-bold">Settings</h3>
            {user ? (
                <div
                    className={`w-full gap-4 p-4 flex  ${user?.person === null ? "flex-row " : "lg:flex-col "} `}
                >
                    <div className="flex-1">
                        <UserSettingsForm
                            userData={user}
                            getData={getUserData}
                        />
                    </div>
                    <div className="flex-1 min-w-0">
                        <PeopleSettingsForm
                            person={user.person ?? null}
                            getData={getUserData}
                        />
                    </div>
                </div>
            ) : (
                <div className="w-full h-full grid grid-cols-2 mt-20 ">
                    <SquareSkeleton mainSize="500px" />
                    <SquareSkeleton mainSize="500px" />
                </div>
            )}
        </div>
    );
};

export default Settings;
