import React, { createContext, useReducer, useContext } from "react";

import {
    useUserStateType as UserState,
    useUserDispatchType as Dispatch,
    useUserInfo,
    useUserInitialState as initialState,
} from "@hooks";

export const UserContext = createContext<
    { state: UserState; dispatch: Dispatch } | undefined
>(undefined);

// exporting context
export const useUser = () => {
    const context = useContext(UserContext);
    if (context === undefined) {
        throw new Error("useUser must be used within a UserContextProvider");
    }
    return context;
};

UserContext.displayName = "UserContext";

type UserContextType = {
    children: React.ReactNode;
};

export const UserContextProvider = ({ children }: UserContextType) => {
    const [state, dispatch] = useReducer(useUserInfo, initialState);
    const value = { state, dispatch };
    return (
        <UserContext.Provider value={value}>{children}</UserContext.Provider>
    );
};
